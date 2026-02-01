import csv
import io
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

import os

from flask import Flask, Response, flash, redirect, render_template, request, url_for

from app.calc import calc_payout_yen, calc_treatment_yen, pick_commission_rule
from app.db import connect, exec1, init_db, now_iso, q, q1


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = os.getenv("SALON_SECRET_KEY", "dev-secret-key")  # ローカル運用想定

    init_db()

    def _find_logo_filename() -> Optional[str]:
        static_dir = app.static_folder or ""
        env_filename = (os.getenv("SALON_LOGO_FILENAME") or "").strip()
        candidates = []
        if env_filename:
            candidates.append(env_filename)
        candidates.extend(["logo.svg", "logo.png", "logo.webp", "logo.jpg", "logo.jpeg"])
        for name in candidates:
            if not name:
                continue
            if os.path.exists(os.path.join(static_dir, name)):
                return name
        return None

    @app.get("/")
    def index():
        today = date.today().isoformat()
        logo_filename = _find_logo_filename()
        logo_url = url_for("static", filename=logo_filename) if logo_filename else None
        logo_alt = (os.getenv("SALON_LOGO_ALT") or "ロゴ").strip() or "ロゴ"
        return render_template("index.html", today=today, logo_url=logo_url, logo_alt=logo_alt)

    @app.get("/lp")
    @app.get("/landing")
    def landing():
        logo_filename = _find_logo_filename()
        logo_url = url_for("static", filename=logo_filename) if logo_filename else None
        logo_alt = (os.getenv("SALON_LOGO_ALT") or "ロゴ").strip() or "ロゴ"
        return render_template("landing.html", logo_url=logo_url, logo_alt=logo_alt)

    @app.get("/admin")
    def admin():
        today = date.today().isoformat()
        conn = connect()
        try:
            supplies = q(
                conn,
                """
                SELECT
                  s.*,
                  (
                    SELECT COUNT(*)
                    FROM supply_alerts sa
                    WHERE sa.supply_id = s.id AND sa.status = 'open'
                  ) AS open_alerts
                FROM supplies s
                WHERE s.is_active = 1
                ORDER BY s.name ASC, s.id ASC
                """,
            )
            supply_alerts = q(
                conn,
                """
                SELECT sa.id, sa.created_at, s.name AS supply_name
                FROM supply_alerts sa
                JOIN supplies s ON s.id = sa.supply_id
                WHERE sa.status = 'open'
                ORDER BY sa.created_at DESC, sa.id DESC
                """,
            )
            return render_template(
                "admin.html",
                today=today,
                supplies=supplies,
                supply_alerts=supply_alerts,
            )
        finally:
            conn.close()

    # ----------------
    # Kiosk (simple input for therapists)
    # ----------------
    @app.get("/kiosk")
    def kiosk():
        # 過去日付も入力できるようにする（デフォルトは今日）
        service_date = (request.args.get("date") or date.today().isoformat()).strip()
        selected_therapist_id = int(request.args.get("therapist_id") or "0")
        conn = connect()
        try:
            therapists, menus = _load_active_therapists_and_menus(conn)
            summaries, details = _compute_daily_summary(conn, service_date)
            items_by_therapist = _kiosk_items_by_therapist(details)
            hpb_totals = _kiosk_hpb_totals(details)
            p_totals = _kiosk_p_totals(details)
            r_totals = _kiosk_r_totals(details)
            recent = _kiosk_recent_treatments(conn, service_date)
            guarantee_rows = q(
                conn,
                "SELECT therapist_id, paid_amount FROM payouts WHERE service_date = ? AND method = ?",
                (service_date, "最低保証"),
            )
            guarantee_map = {int(r["therapist_id"]): int(r["paid_amount"]) for r in guarantee_rows}
            selected_payout = None
            guarantee_lock = False
            if selected_therapist_id > 0:
                payout_row = q1(
                    conn,
                    "SELECT * FROM payouts WHERE service_date = ? AND therapist_id = ?",
                    (service_date, selected_therapist_id),
                )
                if payout_row and str(payout_row["method"]) == "最低保証":
                    selected_payout = payout_row
                    cnt_row = q1(
                        conn,
                        "SELECT COUNT(*) AS cnt FROM treatments WHERE service_date = ? AND therapist_id = ?",
                        (service_date, selected_therapist_id),
                    )
                    guarantee_lock = cnt_row is not None and int(cnt_row["cnt"]) == 0
            return render_template(
                "kiosk.html",
                service_date=service_date,
                therapists=therapists,
                menus=menus,
                summaries=summaries,
                items_by_therapist=items_by_therapist,
                hpb_totals=hpb_totals,
                p_totals=p_totals,
                r_totals=r_totals,
                recent=recent,
                selected_therapist_id=selected_therapist_id,
                selected_payout=selected_payout,
                guarantee_lock=guarantee_lock,
                guarantee_map=guarantee_map,
            )
        finally:
            conn.close()

    @app.post("/kiosk/new")
    def kiosk_new():
        # 過去日付も入力できるようにする（フォーム優先）
        service_date = (request.form.get("service_date") or date.today().isoformat()).strip()
        therapist_id = int(request.form.get("therapist_id") or "0")
        menu_id_1 = int(request.form.get("menu_id_1") or "0")
        menu_id_2 = int(request.form.get("menu_id_2") or "0")
        hpb = int(request.form.get("hpb") or "0")
        p = int(request.form.get("p") or "0")
        r = int(request.form.get("r") or "0")
        continue_add = (request.form.get("continue_add") or "") == "1"
        # シンプル運用: 1回の保存=1件として固定
        quantity = 1

        if therapist_id <= 0 or menu_id_1 <= 0:
            flash("Please select therapist and menu / กรุณาเลือกพนักงานและเมนู", "error")
            return redirect(url_for("kiosk", date=service_date))
        if hpb < 0:
            flash("HPB must be 0+ / HPB ต้องมากกว่าหรือเท่ากับ 0", "error")
            return redirect(url_for("kiosk", date=service_date))
        if p < 0:
            flash("P must be 0+ / P ต้องมากกว่าหรือเท่ากับ 0", "error")
            return redirect(url_for("kiosk", date=service_date))
        if r not in (0, 500, 1000):
            flash("R must be none/500/1000 / R ต้องเป็น ไม่มี/500/1000", "error")
            return redirect(url_for("kiosk", date=service_date))

        conn = connect()
        try:
            # 最大2メニュー。割引/ポイント/指名料は1件目にのみ付けて二重計上を防ぐ。
            conn.execute(
                """
                INSERT INTO treatments(service_date, therapist_id, menu_id, quantity, hpb, p, r, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)
                """,
                (service_date, therapist_id, menu_id_1, quantity, hpb, p, r, now_iso()),
            )
            if menu_id_2 > 0:
                conn.execute(
                    """
                    INSERT INTO treatments(service_date, therapist_id, menu_id, quantity, hpb, p, r, notes, created_at)
                    VALUES (?, ?, ?, ?, 0, 0, 0, NULL, ?)
                    """,
                    (service_date, therapist_id, menu_id_2, quantity, now_iso()),
                )
            conn.commit()
            flash("Saved / บันทึกแล้ว", "ok")
            if continue_add:
                # 同じセラピストで続けてメニューを追加しやすくする
                return redirect(url_for("kiosk", date=service_date, therapist_id=therapist_id, _anchor="input"))
            return redirect(url_for("kiosk", _anchor="summary"))
        finally:
            conn.close()

    @app.post("/kiosk/guarantee")
    def kiosk_guarantee():
        service_date = (request.form.get("service_date") or date.today().isoformat()).strip()
        therapist_id = int(request.form.get("therapist_id") or "0")
        guarantee_amount = int(request.form.get("guarantee_amount") or "0")

        if therapist_id <= 0:
            flash("セラピストを選択してください。", "error")
            return redirect(url_for("kiosk", date=service_date))
        if guarantee_amount <= 0:
            flash("最低保証額を入力してください。", "error")
            return redirect(url_for("kiosk", date=service_date, therapist_id=therapist_id))

        conn = connect()
        try:
            row = q1(
                conn,
                "SELECT COUNT(*) AS cnt FROM treatments WHERE service_date = ? AND therapist_id = ?",
                (service_date, therapist_id),
            )
            if row and int(row["cnt"]) > 0:
                flash("この日に施術があるため最低保証は登録できません。", "error")
                return redirect(url_for("kiosk", date=service_date, therapist_id=therapist_id))

            now = datetime.now().replace(microsecond=0).isoformat()
            conn.execute(
                """
                INSERT INTO payouts(service_date, therapist_id, paid_amount, paid_at, method, notes)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(service_date, therapist_id)
                DO UPDATE SET paid_amount=excluded.paid_amount, paid_at=excluded.paid_at, method=excluded.method, notes=excluded.notes
                """,
                (service_date, therapist_id, guarantee_amount, now, "最低保証", "最低保証"),
            )
            conn.commit()
            flash("最低保証を記録しました。", "ok")
            return redirect(url_for("kiosk", date=service_date, therapist_id=therapist_id, _anchor="summary"))
        finally:
            conn.close()

    @app.post("/kiosk/guarantee/delete")
    def kiosk_guarantee_delete():
        service_date = (request.form.get("service_date") or date.today().isoformat()).strip()
        therapist_id = int(request.form.get("therapist_id") or "0")

        if therapist_id <= 0:
            flash("セラピストを選択してください。", "error")
            return redirect(url_for("kiosk", date=service_date))

        conn = connect()
        try:
            row = q1(
                conn,
                "SELECT * FROM payouts WHERE service_date = ? AND therapist_id = ?",
                (service_date, therapist_id),
            )
            if not row or str(row["method"]) != "最低保証":
                flash("最低保証の記録が見つかりません。", "error")
                return redirect(url_for("kiosk", date=service_date, therapist_id=therapist_id))
            conn.execute(
                "DELETE FROM payouts WHERE service_date = ? AND therapist_id = ?",
                (service_date, therapist_id),
            )
            conn.commit()
            flash("最低保証を削除しました。", "ok")
            return redirect(url_for("kiosk", date=service_date, therapist_id=therapist_id))
        finally:
            conn.close()

    # ----------------
    # Supplies
    # ----------------
    @app.post("/supplies/new")
    def supplies_new():
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("備品名は必須です。", "error")
            return redirect(url_for("admin", _anchor="supplies"))
        conn = connect()
        try:
            existing = q1(conn, "SELECT id FROM supplies WHERE name = ?", (name,))
            if existing:
                flash("同名の備品が既に登録されています。", "error")
                return redirect(url_for("admin", _anchor="supplies"))
            exec1(
                conn,
                """
                INSERT INTO supplies(name, is_active, created_at)
                VALUES (?, 1, ?)
                """,
                (name, now_iso()),
            )
            flash("備品を追加しました。", "ok")
            return redirect(url_for("admin", _anchor="supplies"))
        finally:
            conn.close()

    @app.post("/supplies/<int:supply_id>/notify")
    def supplies_notify(supply_id: int):
        conn = connect()
        try:
            supply = q1(conn, "SELECT * FROM supplies WHERE id = ? AND is_active = 1", (supply_id,))
            if not supply:
                flash("対象の備品が見つかりません。", "error")
                return redirect(url_for("admin", _anchor="supplies"))
            open_alert = q1(
                conn,
                "SELECT id FROM supply_alerts WHERE supply_id = ? AND status = 'open'",
                (supply_id,),
            )
            if open_alert:
                flash("既に通知済みです。", "error")
                return redirect(url_for("admin", _anchor="supplies"))
            exec1(
                conn,
                """
                INSERT INTO supply_alerts(supply_id, status, created_at, acknowledged_at)
                VALUES (?, 'open', ?, NULL)
                """,
                (supply_id, now_iso()),
            )
            flash("不足の通知を送信しました。", "ok")
            return redirect(url_for("admin", _anchor="supplies"))
        finally:
            conn.close()

    @app.post("/supplies/alerts/<int:alert_id>/ack")
    def supply_alert_ack(alert_id: int):
        conn = connect()
        try:
            alert = q1(conn, "SELECT * FROM supply_alerts WHERE id = ?", (alert_id,))
            if not alert:
                flash("対象の通知が見つかりません。", "error")
                return redirect(url_for("admin", _anchor="supplies"))
            if str(alert["status"]) != "open":
                flash("既に対応済みです。", "error")
                return redirect(url_for("admin", _anchor="supplies"))
            conn.execute(
                "UPDATE supply_alerts SET status = 'ack', acknowledged_at = ? WHERE id = ?",
                (now_iso(), alert_id),
            )
            conn.commit()
            flash("通知を対応済みにしました。", "ok")
            return redirect(url_for("admin", _anchor="supplies"))
        finally:
            conn.close()

    @app.get("/kiosk/<int:treatment_id>/edit")
    def kiosk_edit(treatment_id: int):
        view_date = (request.args.get("date") or "").strip() or None
        conn = connect()
        try:
            t = q1(
                conn,
                """
                SELECT t.*, th.name AS therapist_name, m.name AS menu_name
                FROM treatments t
                JOIN therapists th ON th.id = t.therapist_id
                JOIN menus m ON m.id = t.menu_id
                WHERE t.id = ?
                """,
                (treatment_id,),
            )
            if not t:
                flash("対象の入力が見つかりません。", "error")
                return redirect(url_for("kiosk", _anchor="history"))

            service_date = str(t["service_date"])

            therapists = q(conn, "SELECT * FROM therapists ORDER BY is_active DESC, name ASC, id ASC")
            menus = q(conn, "SELECT * FROM menus ORDER BY is_active DESC, name ASC, id ASC")
            return render_template(
                "kiosk_edit.html",
                service_date=service_date,
                treatment=t,
                therapists=therapists,
                menus=menus,
                return_date=view_date or service_date,
            )
        finally:
            conn.close()

    @app.post("/kiosk/<int:treatment_id>/edit")
    def kiosk_edit_post(treatment_id: int):
        therapist_id = int(request.form.get("therapist_id") or "0")
        menu_id = int(request.form.get("menu_id") or "0")
        hpb = int(request.form.get("hpb") or "0")
        p = int(request.form.get("p") or "0")
        r = int(request.form.get("r") or "0")
        return_date = (request.form.get("return_date") or "").strip()

        if therapist_id <= 0 or menu_id <= 0:
            flash("Please select therapist and menu / กรุณาเลือกพนักงานและเมนู", "error")
            return redirect(url_for("kiosk_edit", treatment_id=treatment_id))
        if hpb < 0 or p < 0:
            flash("HPB/P must be 0+ / HPB/P ต้องมากกว่าหรือเท่ากับ 0", "error")
            return redirect(url_for("kiosk_edit", treatment_id=treatment_id))
        if r not in (0, 500, 1000):
            flash("R must be none/500/1000 / R ต้องเป็น ไม่มี/500/1000", "error")
            return redirect(url_for("kiosk_edit", treatment_id=treatment_id))

        conn = connect()
        try:
            row = q1(conn, "SELECT * FROM treatments WHERE id = ?", (treatment_id,))
            if not row:
                flash("対象の入力が見つかりません。", "error")
                return redirect(url_for("kiosk", _anchor="history"))

            conn.execute(
                """
                UPDATE treatments
                SET therapist_id = ?, menu_id = ?, hpb = ?, p = ?, r = ?
                WHERE id = ?
                """,
                (therapist_id, menu_id, hpb, p, r, treatment_id),
            )
            conn.commit()
            flash("Updated / แก้ไขแล้ว", "ok")
            back_date = return_date or str(row["service_date"])
            return redirect(url_for("kiosk", date=back_date, _anchor="history"))
        finally:
            conn.close()

    @app.post("/kiosk/<int:treatment_id>/delete")
    def kiosk_delete(treatment_id: int):
        return_date = (request.form.get("return_date") or "").strip()
        conn = connect()
        try:
            row = q1(conn, "SELECT * FROM treatments WHERE id = ?", (treatment_id,))
            if not row:
                flash("対象の入力が見つかりません。", "error")
                return redirect(url_for("kiosk", _anchor="history"))
            conn.execute("DELETE FROM treatments WHERE id = ?", (treatment_id,))
            conn.commit()
            flash("Deleted / ลบแล้ว", "ok")
            back_date = return_date or str(row["service_date"])
            return redirect(url_for("kiosk", date=back_date, _anchor="history"))
        finally:
            conn.close()

    def _kiosk_items_by_therapist(details: List[Dict[str, object]]) -> Dict[int, List[Dict[str, object]]]:
        """
        Per-therapist item list for the day (for kiosk summary sheet).
        - Do NOT compress as "×2" etc.
        - If quantity > 1 (entered via admin), repeat the line that many times.
        """
        out: Dict[int, List[Dict[str, object]]] = {}
        for d in details:
            tid = int(d["therapist_id"])
            treatment_id = int(d["treatment_id"])
            name = str(d["menu_name"])
            price = int(d.get("price", 0))
            qty = max(1, int(d.get("quantity", 1)))
            if tid not in out:
                out[tid] = []
            for _ in range(qty):
                out[tid].append({"treatment_id": treatment_id, "menu_name": name, "price": price})
        return out

    def _kiosk_hpb_totals(details: List[Dict[str, object]]) -> Dict[int, int]:
        totals: Dict[int, int] = {}
        for d in details:
            tid = int(d["therapist_id"])
            totals[tid] = int(totals.get(tid, 0)) + int(d.get("hpb", 0))
        return totals

    def _kiosk_p_totals(details: List[Dict[str, object]]) -> Dict[int, int]:
        totals: Dict[int, int] = {}
        for d in details:
            tid = int(d["therapist_id"])
            totals[tid] = int(totals.get(tid, 0)) + int(d.get("p", 0))
        return totals

    def _kiosk_r_totals(details: List[Dict[str, object]]) -> Dict[int, int]:
        totals: Dict[int, int] = {}
        for d in details:
            tid = int(d["therapist_id"])
            totals[tid] = int(totals.get(tid, 0)) + int(d.get("r", 0))
        return totals

    def _kiosk_recent_treatments(conn, service_date: str):
        return q(
            conn,
            """
            SELECT t.id, t.created_at, t.hpb, t.p, t.r, th.name AS therapist_name, m.name AS menu_name
            FROM treatments t
            JOIN therapists th ON th.id = t.therapist_id
            JOIN menus m ON m.id = t.menu_id
            WHERE t.service_date = ?
            ORDER BY t.id DESC
            LIMIT 30
            """,
            (service_date,),
        )

    # ----------------
    # Therapists
    # ----------------
    @app.get("/therapists")
    def therapists_list():
        conn = connect()
        try:
            rows = q(
                conn,
                "SELECT * FROM therapists ORDER BY is_active DESC, name ASC, id ASC",
            )
            return render_template("therapists_list.html", therapists=rows)
        finally:
            conn.close()

    @app.post("/therapists/new")
    def therapists_new():
        name = (request.form.get("name") or "").strip()
        commission_type = (request.form.get("commission_type") or "percent").strip()
        commission_value = int(request.form.get("commission_value") or "50")
        is_active = 1 if (request.form.get("is_active") == "on") else 0
        if not name:
            flash("名前は必須です。", "error")
            return redirect(url_for("therapists_list"))
        if commission_type not in ("percent", "fixed"):
            flash("歩合タイプが不正です。", "error")
            return redirect(url_for("therapists_list"))
        conn = connect()
        try:
            exec1(
                conn,
                """
                INSERT INTO therapists(name, commission_type, commission_value, is_active, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (name, commission_type, commission_value, is_active, now_iso()),
            )
            flash("セラピストを追加しました。", "ok")
            return redirect(url_for("therapists_list"))
        finally:
            conn.close()

    @app.post("/therapists/<int:therapist_id>/toggle")
    def therapists_toggle(therapist_id: int):
        conn = connect()
        try:
            t = q1(conn, "SELECT * FROM therapists WHERE id = ?", (therapist_id,))
            if not t:
                flash("対象のセラピストが見つかりません。", "error")
                return redirect(url_for("therapists_list"))
            new_val = 0 if int(t["is_active"]) == 1 else 1
            conn.execute("UPDATE therapists SET is_active = ? WHERE id = ?", (new_val, therapist_id))
            conn.commit()
            flash("ステータスを更新しました。", "ok")
            return redirect(url_for("therapists_list"))
        finally:
            conn.close()

    # ----------------
    # Menus
    # ----------------
    @app.get("/menus")
    def menus_list():
        conn = connect()
        try:
            rows = q(conn, "SELECT * FROM menus ORDER BY is_active DESC, name ASC, id ASC")
            return render_template("menus_list.html", menus=rows)
        finally:
            conn.close()

    @app.get("/menus/<int:menu_id>/edit")
    def menus_edit(menu_id: int):
        conn = connect()
        try:
            m = q1(conn, "SELECT * FROM menus WHERE id = ?", (menu_id,))
            if not m:
                flash("対象のメニューが見つかりません。", "error")
                return redirect(url_for("menus_list"))
            return render_template("menus_edit.html", menu=m)
        finally:
            conn.close()

    @app.post("/menus/<int:menu_id>/edit")
    def menus_edit_post(menu_id: int):
        name = (request.form.get("name") or "").strip()
        price = int(request.form.get("price") or "0")
        is_active = 1 if (request.form.get("is_active") == "on") else 0

        commission_type = (request.form.get("commission_type") or "").strip() or None
        commission_value_raw = (request.form.get("commission_value") or "").strip()
        commission_value = int(commission_value_raw) if commission_value_raw else None

        if not name:
            flash("メニュー名は必須です。", "error")
            return redirect(url_for("menus_edit", menu_id=menu_id))
        if price < 0:
            flash("金額が不正です。", "error")
            return redirect(url_for("menus_edit", menu_id=menu_id))
        if commission_type is not None and commission_type not in ("percent", "fixed"):
            flash("歩合タイプが不正です。", "error")
            return redirect(url_for("menus_edit", menu_id=menu_id))
        if commission_type is None:
            commission_value = None

        conn = connect()
        try:
            m = q1(conn, "SELECT id FROM menus WHERE id = ?", (menu_id,))
            if not m:
                flash("対象のメニューが見つかりません。", "error")
                return redirect(url_for("menus_list"))
            conn.execute(
                """
                UPDATE menus
                SET name = ?, price = ?, commission_type = ?, commission_value = ?, is_active = ?
                WHERE id = ?
                """,
                (name, price, commission_type, commission_value, is_active, menu_id),
            )
            conn.commit()
            flash("メニューを更新しました。", "ok")
            return redirect(url_for("menus_list"))
        finally:
            conn.close()

    @app.post("/menus/new")
    def menus_new():
        name = (request.form.get("name") or "").strip()
        price = int(request.form.get("price") or "0")
        is_active = 1 if (request.form.get("is_active") == "on") else 0

        commission_type = (request.form.get("commission_type") or "").strip() or None
        commission_value_raw = (request.form.get("commission_value") or "").strip()
        commission_value = int(commission_value_raw) if commission_value_raw else None

        if not name:
            flash("メニュー名は必須です。", "error")
            return redirect(url_for("menus_list"))
        if price < 0:
            flash("金額が不正です。", "error")
            return redirect(url_for("menus_list"))
        if commission_type is not None and commission_type not in ("percent", "fixed"):
            flash("歩合タイプが不正です。", "error")
            return redirect(url_for("menus_list"))

        conn = connect()
        try:
            exec1(
                conn,
                """
                INSERT INTO menus(name, price, commission_type, commission_value, is_active, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (name, price, commission_type, commission_value, is_active, now_iso()),
            )
            flash("メニューを追加しました。", "ok")
            return redirect(url_for("menus_list"))
        finally:
            conn.close()

    @app.post("/menus/<int:menu_id>/toggle")
    def menus_toggle(menu_id: int):
        conn = connect()
        try:
            m = q1(conn, "SELECT * FROM menus WHERE id = ?", (menu_id,))
            if not m:
                flash("対象のメニューが見つかりません。", "error")
                return redirect(url_for("menus_list"))
            new_val = 0 if int(m["is_active"]) == 1 else 1
            conn.execute("UPDATE menus SET is_active = ? WHERE id = ?", (new_val, menu_id))
            conn.commit()
            flash("ステータスを更新しました。", "ok")
            return redirect(url_for("menus_list"))
        finally:
            conn.close()

    # ----------------
    # Treatments
    # ----------------
    def _load_active_therapists_and_menus(conn):
        therapists = q(conn, "SELECT * FROM therapists WHERE is_active = 1 ORDER BY name ASC, id ASC")
        menus = q(conn, "SELECT * FROM menus WHERE is_active = 1 ORDER BY name ASC, id ASC")
        return therapists, menus

    @app.get("/treatments")
    def treatments_list():
        service_date = (request.args.get("date") or date.today().isoformat()).strip()
        conn = connect()
        try:
            therapists, menus = _load_active_therapists_and_menus(conn)
            rows = q(
                conn,
                """
                SELECT t.*, th.name AS therapist_name, m.name AS menu_name, m.price AS menu_price
                FROM treatments t
                JOIN therapists th ON th.id = t.therapist_id
                JOIN menus m ON m.id = t.menu_id
                WHERE t.service_date = ?
                ORDER BY t.id DESC
                """,
                (service_date,),
            )
            return render_template(
                "treatments_list.html",
                service_date=service_date,
                therapists=therapists,
                menus=menus,
                treatments=rows,
            )
        finally:
            conn.close()

    @app.post("/treatments/new")
    def treatments_new():
        service_date = (request.form.get("service_date") or date.today().isoformat()).strip()
        therapist_id = int(request.form.get("therapist_id") or "0")
        menu_id = int(request.form.get("menu_id") or "0")
        quantity = int(request.form.get("quantity") or "1")
        notes = (request.form.get("notes") or "").strip() or None
        hpb = int(request.form.get("hpb") or "0")
        p = int(request.form.get("p") or "0")
        r = int(request.form.get("r") or "0")

        price_override_raw = (request.form.get("price_override") or "").strip()
        price_override = int(price_override_raw) if price_override_raw else None

        commission_type_override = (request.form.get("commission_type_override") or "").strip() or None
        commission_value_override_raw = (request.form.get("commission_value_override") or "").strip()
        commission_value_override = int(commission_value_override_raw) if commission_value_override_raw else None

        if therapist_id <= 0 or menu_id <= 0:
            flash("セラピストとメニューを選択してください。", "error")
            return redirect(url_for("treatments_list", date=service_date))
        if quantity <= 0:
            flash("数量が不正です。", "error")
            return redirect(url_for("treatments_list", date=service_date))
        if hpb < 0:
            flash("HPBが不正です。", "error")
            return redirect(url_for("treatments_list", date=service_date))
        if p < 0:
            flash("Pが不正です。", "error")
            return redirect(url_for("treatments_list", date=service_date))
        if r not in (0, 500, 1000):
            flash("Rが不正です。", "error")
            return redirect(url_for("treatments_list", date=service_date))
        if commission_type_override is not None and commission_type_override not in ("percent", "fixed"):
            flash("歩合(上書き)のタイプが不正です。", "error")
            return redirect(url_for("treatments_list", date=service_date))

        conn = connect()
        try:
            exec1(
                conn,
                """
                INSERT INTO treatments(
                  service_date, therapist_id, menu_id, quantity, hpb, p, r,
                  price_override, commission_type_override, commission_value_override,
                  notes, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    service_date,
                    therapist_id,
                    menu_id,
                    quantity,
                    hpb,
                    p,
                    r,
                    price_override,
                    commission_type_override,
                    commission_value_override,
                    notes,
                    now_iso(),
                ),
            )
            flash("施術を記録しました。", "ok")
            return redirect(url_for("treatments_list", date=service_date))
        finally:
            conn.close()

    @app.post("/treatments/<int:treatment_id>/delete")
    def treatments_delete(treatment_id: int):
        service_date = (request.form.get("service_date") or date.today().isoformat()).strip()
        conn = connect()
        try:
            conn.execute("DELETE FROM treatments WHERE id = ?", (treatment_id,))
            conn.commit()
            flash("削除しました。", "ok")
            return redirect(url_for("treatments_list", date=service_date))
        finally:
            conn.close()

    @app.get("/treatments/<int:treatment_id>/edit")
    def treatments_edit(treatment_id: int):
        conn = connect()
        try:
            t = q1(
                conn,
                """
                SELECT t.*, th.name AS therapist_name, m.name AS menu_name
                FROM treatments t
                JOIN therapists th ON th.id = t.therapist_id
                JOIN menus m ON m.id = t.menu_id
                WHERE t.id = ?
                """,
                (treatment_id,),
            )
            if not t:
                flash("対象の施術が見つかりません。", "error")
                return redirect(url_for("treatments_list"))

            therapists = q(conn, "SELECT * FROM therapists ORDER BY is_active DESC, name ASC, id ASC")
            menus = q(conn, "SELECT * FROM menus ORDER BY is_active DESC, name ASC, id ASC")

            return render_template(
                "treatments_edit.html",
                treatment=t,
                therapists=therapists,
                menus=menus,
            )
        finally:
            conn.close()

    @app.post("/treatments/<int:treatment_id>/edit")
    def treatments_edit_post(treatment_id: int):
        service_date = (request.form.get("service_date") or date.today().isoformat()).strip()
        therapist_id = int(request.form.get("therapist_id") or "0")
        menu_id = int(request.form.get("menu_id") or "0")
        quantity = int(request.form.get("quantity") or "1")
        notes = (request.form.get("notes") or "").strip() or None

        hpb = int(request.form.get("hpb") or "0")
        p = int(request.form.get("p") or "0")
        r = int(request.form.get("r") or "0")

        price_override_raw = (request.form.get("price_override") or "").strip()
        price_override = int(price_override_raw) if price_override_raw else None

        commission_type_override = (request.form.get("commission_type_override") or "").strip() or None
        commission_value_override_raw = (request.form.get("commission_value_override") or "").strip()
        commission_value_override = int(commission_value_override_raw) if commission_value_override_raw else None

        if therapist_id <= 0 or menu_id <= 0:
            flash("セラピストとメニューを選択してください。", "error")
            return redirect(url_for("treatments_edit", treatment_id=treatment_id))
        if quantity <= 0:
            flash("数量が不正です。", "error")
            return redirect(url_for("treatments_edit", treatment_id=treatment_id))
        if hpb < 0 or p < 0:
            flash("HPB/Pが不正です。", "error")
            return redirect(url_for("treatments_edit", treatment_id=treatment_id))
        if r not in (0, 500, 1000):
            flash("Rが不正です。", "error")
            return redirect(url_for("treatments_edit", treatment_id=treatment_id))
        if commission_type_override is not None and commission_type_override not in ("percent", "fixed"):
            flash("歩合(上書き)のタイプが不正です。", "error")
            return redirect(url_for("treatments_edit", treatment_id=treatment_id))
        if commission_type_override is None:
            commission_value_override = None

        conn = connect()
        try:
            row = q1(conn, "SELECT id FROM treatments WHERE id = ?", (treatment_id,))
            if not row:
                flash("対象の施術が見つかりません。", "error")
                return redirect(url_for("treatments_list", date=service_date))

            conn.execute(
                """
                UPDATE treatments
                SET
                  service_date = ?,
                  therapist_id = ?,
                  menu_id = ?,
                  quantity = ?,
                  hpb = ?,
                  p = ?,
                  r = ?,
                  price_override = ?,
                  commission_type_override = ?,
                  commission_value_override = ?,
                  notes = ?
                WHERE id = ?
                """,
                (
                    service_date,
                    therapist_id,
                    menu_id,
                    quantity,
                    hpb,
                    p,
                    r,
                    price_override,
                    commission_type_override,
                    commission_value_override,
                    notes,
                    treatment_id,
                ),
            )
            conn.commit()
            flash("施術を更新しました。", "ok")
            return redirect(url_for("treatments_list", date=service_date))
        finally:
            conn.close()

    # ----------------
    # Reports / Payouts
    # ----------------
    def _daily_report_rows(conn, service_date: str):
        rows = q(
            conn,
            """
            SELECT
              t.id,
              t.service_date,
              t.quantity,
              t.hpb,
              t.p,
              t.r,
              t.price_override,
              t.commission_type_override,
              t.commission_value_override,
              t.notes,
              th.id AS therapist_id,
              th.name AS therapist_name,
              th.commission_type AS therapist_commission_type,
              th.commission_value AS therapist_commission_value,
              m.id AS menu_id,
              m.name AS menu_name,
              m.price AS menu_price,
              m.commission_type AS menu_commission_type,
              m.commission_value AS menu_commission_value
            FROM treatments t
            JOIN therapists th ON th.id = t.therapist_id
            JOIN menus m ON m.id = t.menu_id
            WHERE t.service_date = ?
            ORDER BY th.name ASC, t.id ASC
            """,
            (service_date,),
        )
        return rows

    def _compute_daily_summary(conn, service_date: str):
        rows = _daily_report_rows(conn, service_date)
        per_therapist: Dict[int, Dict[str, object]] = {}
        detail_lines: List[Dict[str, object]] = []

        for r in rows:
            price = int(r["price_override"]) if r["price_override"] is not None else int(r["menu_price"])
            rule = pick_commission_rule(
                r["commission_type_override"],
                r["commission_value_override"],
                r["menu_commission_type"],
                r["menu_commission_value"],
                r["therapist_commission_type"],
                r["therapist_commission_value"],
            )
            gross_menu, discount_total, net_menu, sales, payout = calc_treatment_yen(
                unit_price_yen=price,
                quantity=int(r["quantity"]),
                rule=rule,
                hpb_discount_yen=int(r["hpb"] or 0),
                p_points_yen=int(r["p"] or 0),
                r_nomination_fee_yen=int(r["r"] or 0),
            )

            detail_lines.append(
                {
                    "treatment_id": r["id"],
                    "therapist_id": r["therapist_id"],
                    "therapist_name": r["therapist_name"],
                    "menu_name": r["menu_name"],
                    "quantity": int(r["quantity"]),
                    "hpb": int(r["hpb"] or 0),
                    "p": int(r["p"] or 0),
                    "r": int(r["r"] or 0),
                    "price": price,
                    "gross_menu": gross_menu,
                    "discount_total": discount_total,
                    "net_menu": net_menu,
                    "sales": sales,
                    "rule_type": rule.commission_type,
                    "rule_value": rule.commission_value,
                    "payout": payout,
                    "notes": r["notes"] or "",
                }
            )

            tid = int(r["therapist_id"])
            if tid not in per_therapist:
                per_therapist[tid] = {
                    "therapist_id": tid,
                    "therapist_name": r["therapist_name"],
                    "sales_total": 0,
                    "payout_total": 0,
                }
            per_therapist[tid]["sales_total"] = int(per_therapist[tid]["sales_total"]) + sales
            per_therapist[tid]["payout_total"] = int(per_therapist[tid]["payout_total"]) + payout

        payout_rows = q(
            conn,
            """
            SELECT p.*, th.name AS therapist_name
            FROM payouts p
            JOIN therapists th ON th.id = p.therapist_id
            WHERE p.service_date = ?
            """,
            (service_date,),
        )
        paid_map = {int(p["therapist_id"]): p for p in payout_rows}
        for tid, s in per_therapist.items():
            s["paid"] = tid in paid_map
            s["paid_amount"] = int(paid_map[tid]["paid_amount"]) if tid in paid_map else 0
            s["paid_at"] = paid_map[tid]["paid_at"] if tid in paid_map else None
            s["paid_method"] = paid_map[tid]["method"] if tid in paid_map else None

        for tid, p in paid_map.items():
            if tid in per_therapist:
                continue
            per_therapist[tid] = {
                "therapist_id": tid,
                "therapist_name": p["therapist_name"],
                "sales_total": 0,
                "payout_total": int(p["paid_amount"]),
                "paid": True,
                "paid_amount": int(p["paid_amount"]),
                "paid_at": p["paid_at"],
                "paid_method": p["method"],
            }

        summaries = sorted(per_therapist.values(), key=lambda x: (str(x["therapist_name"]), int(x["therapist_id"])))
        return summaries, detail_lines

    @app.get("/reports/daily")
    def report_daily():
        service_date = (request.args.get("date") or date.today().isoformat()).strip()
        conn = connect()
        try:
            summaries, details = _compute_daily_summary(conn, service_date)
            return render_template(
                "report_daily.html",
                service_date=service_date,
                summaries=summaries,
                details=details,
            )
        finally:
            conn.close()

    @app.get("/reports/daily.csv")
    def report_daily_csv():
        service_date = (request.args.get("date") or date.today().isoformat()).strip()
        conn = connect()
        try:
            summaries, details = _compute_daily_summary(conn, service_date)
            output = io.StringIO()
            w = csv.writer(output)

            w.writerow(["日付", service_date])
            w.writerow([])
            w.writerow(["セラピスト別集計"])
            w.writerow(["セラピスト", "売上合計(円)", "支払合計(円)", "支払済み", "支払額(円)", "支払日時", "支払方法"])
            for s in summaries:
                w.writerow(
                    [
                        s["therapist_name"],
                        s["sales_total"],
                        s["payout_total"],
                        "済" if s["paid"] else "未",
                        s["paid_amount"],
                        s["paid_at"] or "",
                        s["paid_method"] or "",
                    ]
                )

            w.writerow([])
            w.writerow(["明細"])
            w.writerow(["施術ID", "セラピスト", "メニュー", "数量", "HPB", "P", "R", "単価(円)", "売上(円)", "歩合タイプ", "歩合値", "支払(円)", "メモ"])
            for d in details:
                w.writerow(
                    [
                        d["treatment_id"],
                        d["therapist_name"],
                        d["menu_name"],
                        d["quantity"],
                        d["hpb"],
                        d["p"],
                        d["r"],
                        d["price"],
                        d["sales"],
                        d["rule_type"],
                        d["rule_value"],
                        d["payout"],
                        d["notes"],
                    ]
                )

            bom = "\ufeff"
            csv_bytes = (bom + output.getvalue()).encode("utf-8")
            filename = f"daily_{service_date}.csv"
            return Response(
                csv_bytes,
                mimetype="text/csv; charset=utf-8",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        finally:
            conn.close()

    @app.get("/reports/payouts.csv")
    def report_payouts_csv():
        conn = connect()
        try:
            rows = q(
                conn,
                """
                SELECT
                  p.service_date,
                  p.therapist_id,
                  th.name AS therapist_name,
                  p.paid_amount,
                  p.paid_at,
                  p.method,
                  p.notes
                FROM payouts p
                JOIN therapists th ON th.id = p.therapist_id
                ORDER BY p.service_date DESC, th.name ASC, p.id ASC
                """,
            )
            output = io.StringIO()
            w = csv.writer(output)
            w.writerow(["日付", "セラピスト", "支払額(円)", "支払日時", "支払方法", "メモ"])
            for r in rows:
                w.writerow(
                    [
                        r["service_date"],
                        r["therapist_name"],
                        r["paid_amount"],
                        r["paid_at"],
                        r["method"] or "",
                        r["notes"] or "",
                    ]
                )

            bom = "\ufeff"
            csv_bytes = (bom + output.getvalue()).encode("utf-8")
            filename = "payouts.csv"
            return Response(
                csv_bytes,
                mimetype="text/csv; charset=utf-8",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        finally:
            conn.close()

    @app.post("/payouts/mark_paid")
    def payout_mark_paid():
        service_date = (request.form.get("service_date") or date.today().isoformat()).strip()
        therapist_id = int(request.form.get("therapist_id") or "0")
        method = (request.form.get("method") or "").strip() or None
        notes = (request.form.get("notes") or "").strip() or None

        conn = connect()
        try:
            summaries, _ = _compute_daily_summary(conn, service_date)
            target = next((s for s in summaries if int(s["therapist_id"]) == therapist_id), None)
            if not target:
                flash("対象のセラピストの集計が見つかりません（施術が未入力かもしれません）。", "error")
                return redirect(url_for("report_daily", date=service_date))

            paid_amount = int(target["payout_total"])
            now = datetime.now().replace(microsecond=0).isoformat()
            conn.execute(
                """
                INSERT INTO payouts(service_date, therapist_id, paid_amount, paid_at, method, notes)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(service_date, therapist_id)
                DO UPDATE SET paid_amount=excluded.paid_amount, paid_at=excluded.paid_at, method=excluded.method, notes=excluded.notes
                """,
                (service_date, therapist_id, paid_amount, now, method, notes),
            )
            conn.commit()
            flash("支払い済みにしました。", "ok")
            return redirect(url_for("report_daily", date=service_date))
        finally:
            conn.close()

    @app.post("/payouts/unmark_paid")
    def payout_unmark_paid():
        service_date = (request.form.get("service_date") or date.today().isoformat()).strip()
        therapist_id = int(request.form.get("therapist_id") or "0")
        conn = connect()
        try:
            conn.execute("DELETE FROM payouts WHERE service_date = ? AND therapist_id = ?", (service_date, therapist_id))
            conn.commit()
            flash("支払い記録を取り消しました。", "ok")
            return redirect(url_for("report_daily", date=service_date))
        finally:
            conn.close()

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)


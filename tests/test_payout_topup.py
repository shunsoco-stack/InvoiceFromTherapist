import csv
import io
import os
import unittest
from pathlib import Path


TEST_DB = Path(__file__).resolve().parent.parent / ".test_payout_topup.sqlite3"
OLD_DB_PATH = os.environ.get("SALON_DB_PATH")
os.environ["SALON_DB_PATH"] = str(TEST_DB)

from app.app import create_app  # noqa: E402
from app.db import connect, now_iso  # noqa: E402


class PayoutTopupTests(unittest.TestCase):
    service_date = "2026-08-01"

    def setUp(self):
        os.environ["SALON_DB_PATH"] = str(TEST_DB)
        self._remove_test_db()
        self.app = create_app()
        self.client = self.app.test_client()

        conn = connect()
        try:
            now = now_iso()
            self.therapist_id = conn.execute(
                """
                INSERT INTO therapists(name, commission_type, commission_value, is_active, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("テストスタッフ", "percent", 50, 1, now),
            ).lastrowid
            self.menu_id = conn.execute(
                """
                INSERT INTO menus(display_id, name, price, coupon_discount, commission_type, commission_value, is_active, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (1, "5,000円コース", 5000, 0, None, None, 1, now),
            ).lastrowid
            conn.commit()
        finally:
            conn.close()

    def tearDown(self):
        self._remove_test_db()
        if OLD_DB_PATH is None:
            os.environ.pop("SALON_DB_PATH", None)
        else:
            os.environ["SALON_DB_PATH"] = OLD_DB_PATH

    def _remove_test_db(self):
        for path in (TEST_DB, Path(str(TEST_DB) + "-wal"), Path(str(TEST_DB) + "-shm")):
            if path.exists():
                path.unlink()

    def _add_treatment(self, *, therapist_id=None, menu_id=None, count_as_customer=1):
        conn = connect()
        try:
            conn.execute(
                """
                INSERT INTO treatments(
                  service_date, therapist_id, menu_id, quantity, hpb, p, r, count_as_customer, created_at
                )
                VALUES (?, ?, ?, 1, 0, 0, 0, ?, ?)
                """,
                (
                    self.service_date,
                    therapist_id or self.therapist_id,
                    menu_id or self.menu_id,
                    count_as_customer,
                    now_iso(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _save_topup(self, amount, *, therapist_id=None, follow_redirects=False):
        return self.client.post(
            "/kiosk/payout-topup",
            data={
                "service_date": self.service_date,
                "therapist_id": therapist_id or self.therapist_id,
                "topup_amount": amount,
            },
            follow_redirects=follow_redirects,
        )

    def _daily_summary_rows(self):
        response = self.client.get(f"/reports/daily.csv?date={self.service_date}")
        self.assertEqual(response.status_code, 200)
        rows = list(csv.reader(io.StringIO(response.data.decode("utf-8-sig"))))
        header = [
            "セラピスト",
            "客数",
            "売上合計(円)",
            "施術分(円)",
            "不足分(円)",
            "支払合計(円)",
            "支払済み",
            "支払額(円)",
            "支払日時",
            "支払方法",
        ]
        header_index = rows.index(header)
        result = {}
        for row in rows[header_index + 1 :]:
            if not row:
                break
            result[row[0]] = row
        return result

    def test_manual_topup_makes_2500_commission_a_5000_payout(self):
        self._add_treatment()
        before_topup = self._daily_summary_rows()["テストスタッフ"]
        self.assertEqual(before_topup[3:6], ["2500", "0", "2500"])
        sales_before = self.client.get(
            f"/reports/sales_daily.csv?date_from={self.service_date}&date_to={self.service_date}"
        ).data

        response = self._save_topup(2500)

        self.assertEqual(response.status_code, 302)
        conn = connect()
        try:
            topup = conn.execute(
                "SELECT amount FROM payout_topups WHERE service_date = ? AND therapist_id = ?",
                (self.service_date, self.therapist_id),
            ).fetchone()
            payout_count = conn.execute("SELECT COUNT(*) FROM payouts").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(topup["amount"], 2500)
        self.assertEqual(payout_count, 0, "不足分の保存だけでは支払済みにしない")

        summary = self._daily_summary_rows()["テストスタッフ"]
        self.assertEqual(summary[1:7], ["1", "5000", "2500", "2500", "5000", "未"])

        sales_after = self.client.get(
            f"/reports/sales_daily.csv?date_from={self.service_date}&date_to={self.service_date}"
        ).data
        self.assertEqual(sales_after, sales_before, "不足分は売上・客数CSVを変更しない")

        kiosk_body = self.client.get(f"/kiosk?date={self.service_date}").get_data(as_text=True)
        self.assertIn("เงินเพิ่มให้ครบ 5,000 เยน", kiosk_body)
        self.assertIn("รับทั้งหมด / 支払合計", kiosk_body)

        staff_csv = self.client.get(
            f"/reports/staff.csv?date_from={self.service_date}&date_to={self.service_date}"
        ).data.decode("utf-8-sig")
        self.assertIn("最低保証の不足分", staff_csv)
        self.assertIn(",2500,0,手動入力", staff_csv)

    def test_topup_cannot_exceed_the_current_shortfall(self):
        self._add_treatment()

        response = self._save_topup(3000, follow_redirects=True)

        self.assertIn("不足分は最大2,500円です", response.get_data(as_text=True))
        conn = connect()
        try:
            topup_count = conn.execute("SELECT COUNT(*) FROM payout_topups").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(topup_count, 0)

    def test_mark_paid_uses_total_and_blocks_topup_changes_until_cancelled(self):
        self._add_treatment()
        self._save_topup(2500)

        paid_response = self.client.post(
            "/payouts/mark_paid",
            data={
                "service_date": self.service_date,
                "therapist_id": self.therapist_id,
                "method": "現金",
            },
        )
        self.assertEqual(paid_response.status_code, 302)

        conn = connect()
        try:
            paid_amount = conn.execute(
                "SELECT paid_amount FROM payouts WHERE service_date = ? AND therapist_id = ?",
                (self.service_date, self.therapist_id),
            ).fetchone()["paid_amount"]
        finally:
            conn.close()
        self.assertEqual(paid_amount, 5000)

        blocked = self._save_topup(2000, follow_redirects=True)
        self.assertIn("支払済みを取り消してから不足分を変更", blocked.get_data(as_text=True))
        conn = connect()
        try:
            amount = conn.execute("SELECT amount FROM payout_topups").fetchone()["amount"]
        finally:
            conn.close()
        self.assertEqual(amount, 2500)

        self.client.post(
            "/payouts/unmark_paid",
            data={"service_date": self.service_date, "therapist_id": self.therapist_id},
        )
        self._save_topup(2000)
        summary = self._daily_summary_rows()["テストスタッフ"]
        self.assertEqual(summary[3:6], ["2500", "2000", "4500"])

    def test_payment_is_blocked_until_daily_total_reaches_5000(self):
        self._add_treatment()

        individual = self.client.post(
            "/payouts/mark_paid",
            data={"service_date": self.service_date, "therapist_id": self.therapist_id},
            follow_redirects=True,
        )
        self.assertIn("不足分2,500円を入力してから支払済み", individual.get_data(as_text=True))

        bulk = self.client.post(
            "/payouts/mark_paid_all",
            data={"service_date": self.service_date},
            follow_redirects=True,
        )
        self.assertIn("支払合計が5,000円未満", bulk.get_data(as_text=True))
        conn = connect()
        try:
            payout_count = conn.execute("SELECT COUNT(*) FROM payouts").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(payout_count, 0)

        self._save_topup(2500)
        completed = self.client.post(
            "/payouts/mark_paid_all",
            data={"service_date": self.service_date},
        )
        self.assertEqual(completed.status_code, 302)
        conn = connect()
        try:
            paid_amount = conn.execute("SELECT paid_amount FROM payouts").fetchone()["paid_amount"]
        finally:
            conn.close()
        self.assertEqual(paid_amount, 5000)

    def test_topup_can_be_updated_and_deleted_without_locking_treatments(self):
        self._add_treatment()
        self._save_topup(2000)
        partial_body = self.client.get(f"/kiosk?date={self.service_date}").get_data(as_text=True)
        self.assertIn("入力する不足分：2,500円", partial_body)
        self._save_topup(2500)

        conn = connect()
        try:
            count, amount = conn.execute(
                "SELECT COUNT(*), MAX(amount) FROM payout_topups"
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(count, 1)
        self.assertEqual(amount, 2500)

        second_treatment = self.client.post(
            "/kiosk/new",
            data={
                "service_date": self.service_date,
                "therapist_id": self.therapist_id,
                "menu_id_1": self.menu_id,
                "menu_id_2": 0,
                "hpb": 0,
                "p": 0,
                "r": 0,
            },
        )
        self.assertEqual(second_treatment.status_code, 302)
        summary = self._daily_summary_rows()["テストスタッフ"]
        self.assertEqual(summary[1:6], ["2", "10000", "5000", "2500", "7500"])

        stale_individual = self.client.post(
            "/payouts/mark_paid",
            data={"service_date": self.service_date, "therapist_id": self.therapist_id},
            follow_redirects=True,
        )
        self.assertIn("登録済みの不足分を削除", stale_individual.get_data(as_text=True))
        stale_bulk = self.client.post(
            "/payouts/mark_paid_all",
            data={"service_date": self.service_date},
            follow_redirects=True,
        )
        self.assertIn("不足分が現在の施術分と合いません", stale_bulk.get_data(as_text=True))
        conn = connect()
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM payouts").fetchone()[0], 0)
        finally:
            conn.close()

        deleted = self.client.post(
            "/kiosk/payout-topup/delete",
            data={"service_date": self.service_date, "therapist_id": self.therapist_id},
        )
        self.assertEqual(deleted.status_code, 302)
        summary = self._daily_summary_rows()["テストスタッフ"]
        self.assertEqual(summary[3:6], ["5000", "0", "5000"])

    def test_deleting_the_last_treatment_removes_its_topup(self):
        self._add_treatment()
        self._save_topup(2500)
        conn = connect()
        try:
            treatment_id = conn.execute("SELECT id FROM treatments").fetchone()["id"]
        finally:
            conn.close()

        response = self.client.post(
            f"/kiosk/{treatment_id}/delete",
            data={"return_date": self.service_date},
        )

        self.assertEqual(response.status_code, 302)
        conn = connect()
        try:
            treatment_count = conn.execute("SELECT COUNT(*) FROM treatments").fetchone()[0]
            topup_count = conn.execute("SELECT COUNT(*) FROM payout_topups").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual((treatment_count, topup_count), (0, 0))

    def test_admin_delete_and_staff_moves_clean_only_orphaned_topups(self):
        self._add_treatment()
        self._save_topup(2500)
        self._add_treatment()
        conn = connect()
        try:
            treatment_ids = [r["id"] for r in conn.execute("SELECT id FROM treatments ORDER BY id")]
        finally:
            conn.close()

        self.client.post(
            f"/treatments/{treatment_ids[0]}/delete",
            data={"service_date": self.service_date},
        )
        conn = connect()
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM treatments").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM payout_topups").fetchone()[0], 1)
            second_therapist_id = conn.execute(
                """
                INSERT INTO therapists(name, commission_type, commission_value, is_active, created_at)
                VALUES (?, 'percent', 50, 1, ?)
                """,
                ("移動先スタッフ", now_iso()),
            ).lastrowid
            conn.commit()
        finally:
            conn.close()

        remaining_id = treatment_ids[1]
        self.client.post(
            f"/treatments/{remaining_id}/edit",
            data={
                "service_date": self.service_date,
                "therapist_id": second_therapist_id,
                "menu_id": self.menu_id,
                "quantity": 1,
                "hpb": 0,
                "p": 0,
                "r": 0,
            },
        )
        conn = connect()
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM payout_topups").fetchone()[0], 0)
        finally:
            conn.close()

        self._save_topup(2500, therapist_id=second_therapist_id)
        self.client.post(
            f"/kiosk/{remaining_id}/edit",
            data={
                "return_date": self.service_date,
                "therapist_id": self.therapist_id,
                "menu_id": self.menu_id,
                "hpb": 0,
                "p": 0,
                "r": 0,
            },
        )
        conn = connect()
        try:
            moved_to = conn.execute("SELECT therapist_id FROM treatments WHERE id = ?", (remaining_id,)).fetchone()[0]
            self.assertEqual(moved_to, self.therapist_id)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM payout_topups").fetchone()[0], 0)
        finally:
            conn.close()

    def test_paid_day_blocks_treatment_add_edit_and_delete(self):
        self._add_treatment()
        self._save_topup(2500)
        self.client.post(
            "/payouts/mark_paid",
            data={"service_date": self.service_date, "therapist_id": self.therapist_id},
        )
        conn = connect()
        try:
            treatment_id = conn.execute("SELECT id FROM treatments").fetchone()[0]
        finally:
            conn.close()

        kiosk_add = self.client.post(
            "/kiosk/new",
            data={
                "service_date": self.service_date,
                "therapist_id": self.therapist_id,
                "menu_id_1": self.menu_id,
                "menu_id_2": 0,
                "hpb": 0,
                "p": 0,
                "r": 0,
            },
            follow_redirects=True,
        )
        self.assertIn("支払済みを取り消してから施術を追加", kiosk_add.get_data(as_text=True))
        admin_add = self.client.post(
            "/treatments/new",
            data={
                "service_date": self.service_date,
                "therapist_id": self.therapist_id,
                "menu_id": self.menu_id,
                "quantity": 1,
                "hpb": 0,
                "p": 0,
                "r": 0,
            },
            follow_redirects=True,
        )
        self.assertIn("支払済みを取り消してから施術を追加", admin_add.get_data(as_text=True))

        for endpoint, data in (
            (
                f"/kiosk/{treatment_id}/edit",
                {
                    "return_date": self.service_date,
                    "therapist_id": self.therapist_id,
                    "menu_id": self.menu_id,
                    "hpb": 0,
                    "p": 0,
                    "r": 0,
                },
            ),
            (
                f"/treatments/{treatment_id}/edit",
                {
                    "service_date": self.service_date,
                    "therapist_id": self.therapist_id,
                    "menu_id": self.menu_id,
                    "quantity": 1,
                    "hpb": 0,
                    "p": 0,
                    "r": 0,
                },
            ),
            (f"/kiosk/{treatment_id}/delete", {"return_date": self.service_date}),
            (f"/treatments/{treatment_id}/delete", {"service_date": self.service_date}),
        ):
            response = self.client.post(endpoint, data=data, follow_redirects=True)
            self.assertIn("支払済みを取り消してから施術", response.get_data(as_text=True))

        conn = connect()
        try:
            counts = (
                conn.execute("SELECT COUNT(*) FROM treatments").fetchone()[0],
                conn.execute("SELECT COUNT(*) FROM payout_topups").fetchone()[0],
                conn.execute("SELECT COUNT(*) FROM payouts").fetchone()[0],
            )
        finally:
            conn.close()
        self.assertEqual(counts, (1, 1, 1))

    def test_no_treatment_uses_existing_legacy_guarantee(self):
        rejected = self._save_topup(5000, follow_redirects=True)
        self.assertIn("施術がない日は「最低保証」を使ってください", rejected.get_data(as_text=True))

        too_small = self.client.post(
            "/kiosk/guarantee",
            data={
                "service_date": self.service_date,
                "therapist_id": self.therapist_id,
                "guarantee_amount": 1000,
            },
            follow_redirects=True,
        )
        self.assertIn("最低保証額は5,000円以上", too_small.get_data(as_text=True))
        invalid = self.client.post(
            "/kiosk/guarantee",
            data={
                "service_date": self.service_date,
                "therapist_id": self.therapist_id,
                "guarantee_amount": "abc",
            },
            follow_redirects=True,
        )
        self.assertIn("最低保証額は数字", invalid.get_data(as_text=True))

        guarantee = self.client.post(
            "/kiosk/guarantee",
            data={
                "service_date": self.service_date,
                "therapist_id": self.therapist_id,
                "guarantee_amount": 5000,
            },
        )
        self.assertEqual(guarantee.status_code, 302)

        conn = connect()
        try:
            payout = conn.execute(
                "SELECT paid_amount, method FROM payouts WHERE service_date = ? AND therapist_id = ?",
                (self.service_date, self.therapist_id),
            ).fetchone()
            topup_count = conn.execute("SELECT COUNT(*) FROM payout_topups").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual((payout["paid_amount"], payout["method"]), (5000, "最低保証"))
        self.assertEqual(topup_count, 0)

        blocked_add = self.client.post(
            "/kiosk/new",
            data={
                "service_date": self.service_date,
                "therapist_id": self.therapist_id,
                "menu_id_1": self.menu_id,
                "menu_id_2": 0,
                "hpb": 0,
                "p": 0,
                "r": 0,
            },
            follow_redirects=True,
        )
        self.assertIn("支払済みを取り消してから施術を追加", blocked_add.get_data(as_text=True))
        conn = connect()
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM treatments").fetchone()[0], 0)
        finally:
            conn.close()

        summary = self._daily_summary_rows()["テストスタッフ"]
        self.assertEqual(summary[1:7], ["0", "0", "0", "5000", "5000", "済"])

        staff_report = self.client.get(
            f"/reports/staff?date_from={self.service_date}&date_to={self.service_date}"
        ).get_data(as_text=True)
        self.assertIn("保証・不足分", staff_report)
        self.assertIn("5,000円", staff_report)
        staff_csv = self.client.get(
            f"/reports/staff.csv?date_from={self.service_date}&date_to={self.service_date}"
        ).data.decode("utf-8-sig")
        self.assertIn("最低保証（客数0人）", staff_csv)
        self.assertIn(",5000,0,支払済み", staff_csv)


if __name__ == "__main__":
    unittest.main()

import os
import unittest
from pathlib import Path


TEST_DB = Path(__file__).resolve().parent.parent / ".test_monthly_report.sqlite3"
OLD_DB_PATH = os.environ.get("SALON_DB_PATH")
os.environ["SALON_DB_PATH"] = str(TEST_DB)
if TEST_DB.exists():
    TEST_DB.unlink()

from app.app import create_app  # noqa: E402
from app.db import connect, now_iso  # noqa: E402


class MonthlyReportTests(unittest.TestCase):
    def setUp(self):
        os.environ["SALON_DB_PATH"] = str(TEST_DB)
        if TEST_DB.exists():
            TEST_DB.unlink()
        self.app = create_app()
        self.client = self.app.test_client()

    def tearDown(self):
        if TEST_DB.exists():
            TEST_DB.unlink()
        if OLD_DB_PATH is None:
            os.environ.pop("SALON_DB_PATH", None)
        else:
            os.environ["SALON_DB_PATH"] = OLD_DB_PATH

    def test_monthly_calendar_totals_sales_and_customers(self):
        conn = connect()
        try:
            now = now_iso()
            therapist_id = conn.execute(
                """
                INSERT INTO therapists(name, commission_type, commission_value, is_active, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("ミリン", "percent", 50, 1, now),
            ).lastrowid
            menu_id = conn.execute(
                """
                INSERT INTO menus(display_id, name, price, coupon_discount, commission_type, commission_value, is_active, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (1, "T/60", 10000, 0, None, None, 1, now),
            ).lastrowid
            option_menu_id = conn.execute(
                """
                INSERT INTO menus(display_id, name, price, coupon_discount, commission_type, commission_value, is_active, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (2, "Option", 5000, 0, None, None, 1, now),
            ).lastrowid
            conn.execute(
                """
                INSERT INTO treatments(service_date, therapist_id, menu_id, quantity, hpb, p, r, count_as_customer, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("2026-07-05", therapist_id, menu_id, 2, 1000, 500, 300, 1, now),
            )
            conn.execute(
                """
                INSERT INTO treatments(service_date, therapist_id, menu_id, quantity, hpb, p, r, count_as_customer, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("2026-07-05", therapist_id, option_menu_id, 1, 0, 0, 0, 0, now),
            )
            conn.execute(
                """
                INSERT INTO treatments(service_date, therapist_id, menu_id, quantity, hpb, p, r, count_as_customer, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("2026-07-10", therapist_id, option_menu_id, 1, 0, 0, 0, 1, now),
            )
            conn.commit()
        finally:
            conn.close()

        response = self.client.get("/reports/monthly?month=2026-07")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("2026年7月", body)
        self.assertIn("28,800円", body)
        self.assertIn("総客数", body)
        self.assertIn("3人", body)
        self.assertIn("売上 23,800円", body)
        self.assertIn("客数 2人", body)


if __name__ == "__main__":
    unittest.main()

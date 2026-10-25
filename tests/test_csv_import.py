import unittest

from app.csv_import import extract_existing_sales_dates, normalize_sales_date


class CsvImportTests(unittest.TestCase):
    def test_extracts_cp932_card_transaction_dates(self):
        text = "\r\n".join(
            [
                '"取引日","取引時間","取引内容","金額（円）"',
                '"2026-03-30","21:56:37","売上","6000"',
                '"2026-03-30","21:58:00","取消","-6000"',
                '"2026-03-29","22:03:20","売上","12000"',
            ]
        )

        dates = extract_existing_sales_dates(text.encode("cp932"))

        self.assertEqual(dates, {"2026-03-29", "2026-03-30"})

    def test_extracts_salesops_date_column(self):
        text = "発生日,売上,客数\r\n2026-03-28,10000,2\r\n"

        dates = extract_existing_sales_dates(text.encode("utf-8-sig"))

        self.assertEqual(dates, {"2026-03-28"})

    def test_normalizes_japanese_date_text(self):
        self.assertEqual(normalize_sales_date("2026年3月7日"), "2026-03-07")


if __name__ == "__main__":
    unittest.main()

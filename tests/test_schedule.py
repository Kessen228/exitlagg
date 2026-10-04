import unittest
from datetime import date, datetime

from launcher_scheduler.schedule import (
    format_days, format_duration, format_moment, latest_occurrence, next_occurrence, parse_hhmm,
)

MON = datetime(2026, 10, 5)


class ScheduleTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_hhmm("04:05").hour, 4)
        self.assertEqual(parse_hhmm("23:59").minute, 59)
        with self.assertRaises(ValueError):
            parse_hhmm("25:00")

    def test_latest_occurrence_same_day(self):
        self.assertEqual(latest_occurrence("04:00", range(7), MON.replace(hour=5)), MON.replace(hour=4))

    def test_latest_occurrence_previous_day(self):
        now = MON.replace(hour=3)
        self.assertEqual(latest_occurrence("04:00", range(7), now), datetime(2026, 10, 4, 4, 0))

    def test_latest_occurrence_respects_days(self):
        # только пятница: последний запуск — в прошлую пятницу
        self.assertEqual(latest_occurrence("04:00", [4], MON.replace(hour=12)), datetime(2026, 10, 2, 4, 0))
        self.assertIsNone(latest_occurrence("04:00", [], MON))

    def test_next_occurrence(self):
        self.assertEqual(next_occurrence("04:00", range(7), MON.replace(hour=3)), MON.replace(hour=4))
        self.assertEqual(next_occurrence("04:00", range(7), MON.replace(hour=4)), datetime(2026, 10, 6, 4, 0))
        self.assertEqual(next_occurrence("04:00", [0], MON.replace(hour=5)), datetime(2026, 10, 12, 4, 0))

    def test_format(self):
        self.assertEqual(format_days(range(7)), "Каждый день")
        self.assertEqual(format_days([0, 1, 2, 3, 4]), "Будни")
        self.assertEqual(format_days([6, 5]), "Выходные")
        self.assertEqual(format_days([0, 2]), "Пн, Ср")
        self.assertEqual(format_duration(0), "Не закрывать")
        self.assertEqual(format_duration(150), "2 ч 30 мин")
        self.assertEqual(format_duration(120), "2 ч")
        self.assertEqual(format_duration(45), "45 мин")
        today = date(2026, 10, 5)
        self.assertEqual(format_moment(datetime(2026, 10, 5, 4, 0), today), "сегодня 04:00")
        self.assertEqual(format_moment(datetime(2026, 10, 6, 4, 0), today), "завтра 04:00")
        self.assertEqual(format_moment(datetime(2026, 10, 9, 4, 0), today), "Пт 09.10 04:00")


if __name__ == "__main__":
    unittest.main()

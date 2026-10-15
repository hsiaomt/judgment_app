import tkinter as tk
import unittest
from unittest.mock import patch

from judgment_app.case_number import CaseNumber
from judgment_app.gui import CaseReaderApp


class CourtSearchTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = CaseReaderApp(self.root)

    def tearDown(self):
        for callback in self.root.tk.call("after", "info"):
            self.root.after_cancel(callback)
        self.root.destroy()

    def matching_codes(self):
        return {self.app.manual_courts[label] for label in self.app.court_box["values"]}

    def test_typing_filters_names_codes_and_aliases(self):
        app = self.app
        for query, expected in (("台北", {"TPD", "TPB", "TPT"}),
                                ("臺北", {"TPD", "TPB", "TPT"}),
                                (" tpd ", {"TPD"}), ("北院", {"TPD"}),
                                ("台北地院", {"TPD"}), ("板橋地院", {"PCD"}),
                                ("中高分院", {"TCH"})):
            with self.subTest(query=query):
                app.manual_court.set(query)
                self.assertEqual(self.matching_codes(), expected)
                self.assertEqual(app.manual_court.get(), query)
        app.manual_court.set("")
        self.assertEqual(self.matching_codes(), set(app.manual_courts.values()))

    def test_enter_confirms_unique_match_and_restores_full_list(self):
        app = self.app
        app.manual_court.set("tpd")
        self.assertTrue(app.court_box.bind("<Return>"))
        self.assertEqual(app.confirm_manual_court(), "break")
        self.assertEqual(app.manual_courts[app.manual_court.get()], "TPD")
        self.assertEqual(self.matching_codes(), set(app.manual_courts.values()))
        app.manual_court.set("板橋地院")
        app.confirm_manual_court()
        self.assertEqual(app.manual_courts[app.manual_court.get()], "PCD")

    def test_ambiguous_input_opens_choices_without_selecting_first(self):
        app = self.app
        app.manual_court.set("高等")
        with patch.object(app.court_box, "event_generate") as generate:
            app.confirm_manual_court(event=object())
        generate.assert_called_once_with("<Down>")
        self.assertEqual(app.manual_court.get(), "高等")
        self.assertGreater(len(app.court_box["values"]), 1)

    def test_invalid_or_ambiguous_input_cannot_add_previous_court(self):
        app = self.app
        app.manual_year.set("101")
        app.manual_case.set("金訴")
        app.manual_number.set("60")
        for query in ("不存在的法院", "台北", ""):
            with self.subTest(query=query), patch("judgment_app.gui.download_page.messagebox.showerror") as error:
                app.manual_court.set(query)
                app.add_case()
                error.assert_called_once()
                self.assertEqual(app.cases, [])
        app.manual_court.set("不存在的法院")
        self.assertEqual(tuple(app.court_box["values"]), ())

    def test_unique_search_and_dropdown_selection_add_correct_case(self):
        app = self.app
        app.manual_year.set("101")
        app.manual_case.set("金訴")
        app.manual_number.set("60")
        app.manual_court.set("北院")
        app.add_case()
        self.assertEqual(app.visible_cases(), [CaseNumber("TPD", 101, "金訴", 60)])

        app.manual_court.set("台北")
        label = next(label for label in app.court_box["values"] if app.manual_courts[label] == "TPT")
        app.court_box.set(label)
        app.court_box.event_generate("<<ComboboxSelected>>")
        app.add_case()
        self.assertEqual(app.visible_cases()[-1], CaseNumber("TPT", 101, "金訴", 60))

    def test_busy_state_restores_editable_court_input(self):
        app = self.app
        app.manual_court.set("tpd")
        app.set_busy(True)
        self.assertEqual(str(app.court_box["state"]), "disabled")
        app.confirm_manual_court()
        self.assertEqual(app.manual_court.get(), "tpd")
        app.set_busy(False)
        self.assertEqual(str(app.court_box["state"]), "normal")
        app.confirm_manual_court()
        self.assertEqual(app.manual_courts[app.manual_court.get()], "TPD")

    def test_control_a_selects_existing_court_for_replacement(self):
        self.app.select_court_text()
        self.assertTrue(self.app.court_box.selection_present())
        self.assertEqual(self.app.court_box.index("sel.first"), 0)
        self.assertEqual(self.app.court_box.index("sel.last"), len(self.app.manual_court.get()))


if __name__ == "__main__":
    unittest.main()

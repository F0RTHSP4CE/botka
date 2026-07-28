from botka.handlers.menu import Btn, main_menu_kb


def _button_texts(keyboard) -> list[str]:
    return [button.text for row in keyboard.keyboard for button in row]


def test_removed_buttons_are_not_in_resident_menu() -> None:
    button_texts = _button_texts(main_menu_kb())

    assert Btn.TRANSFER not in button_texts
    assert Btn.BORROWED not in button_texts
    assert Btn.AGENDA not in button_texts
    assert Btn.TRANSACTIONS not in button_texts
    assert Btn.UPS not in button_texts


def test_resident_menu_has_all_remaining_buttons_on_one_page() -> None:
    keyboard = main_menu_kb()
    button_texts = _button_texts(keyboard)

    assert Btn.CREATE_QUEST in button_texts
    assert Btn.BAMBU in button_texts
    assert Btn.BAMBU == "🖨️ 3D printers status"
    assert len(keyboard.keyboard) == 6
    assert all(len(row) == 2 for row in keyboard.keyboard)

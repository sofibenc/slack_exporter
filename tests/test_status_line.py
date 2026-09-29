import io

from slack_exporter.cli import StatusLine

CLEAR = "\r\x1b[2K"


class FakeTerminal(io.StringIO):
    def isatty(self):
        return True


def test_status_is_rewritten_in_place_on_a_terminal():
    out = FakeTerminal()
    status = StatusLine(out, width=80)

    status.update("⏳ longue ligne")
    status.update("⏳ court")

    assert out.getvalue() == CLEAR + "⏳ longue ligne" + CLEAR + "⏳ court"


def test_final_line_replaces_the_status():
    out = FakeTerminal()
    status = StatusLine(out, width=80)

    status.update("⏳ abc")
    status.line("✓ fini")
    status.update("⏳ suivant")

    assert out.getvalue() == CLEAR + "⏳ abc" + CLEAR + "✓ fini\n" + CLEAR + "⏳ suivant"


def test_note_keeps_the_status_below_it():
    out = FakeTerminal()
    status = StatusLine(out, width=80)

    status.update("⏳ abc")
    status.note("Limite Slack atteinte")

    assert out.getvalue() == CLEAR + "⏳ abc" + CLEAR + "Limite Slack atteinte\n" + CLEAR + "⏳ abc"


def test_status_is_truncated_to_terminal_width():
    out = FakeTerminal()
    StatusLine(out, width=10).update("x" * 50)
    assert out.getvalue() == CLEAR + "x" * 9


def test_wide_characters_count_as_two_columns():
    # ⏳ occupe deux colonnes : la ligne ne doit jamais déborder du terminal.
    out = FakeTerminal()
    StatusLine(out, width=10).update("⏳" + "x" * 50)
    assert out.getvalue() == CLEAR + "⏳" + "x" * 7


def test_no_status_when_output_is_not_a_terminal():
    out = io.StringIO()
    status = StatusLine(out, width=80)

    status.update("⏳ abc")
    status.line("✓ fini")
    status.note("note")

    assert out.getvalue() == "✓ fini\nnote\n"

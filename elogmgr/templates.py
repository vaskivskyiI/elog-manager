"""Built-in starting points for new logbooks.

Users who do not administer a logbook yet cannot copy one of their own,
so they start from one of these.  Managers can copy any logbook as well.
"""

PREFIX = "builtin:"

TEMPLATES: dict[str, list[tuple[str, str]]] = {
    "Experiment (runs)": [
        ("Attributes", "Author, Category, File, Sample, RunN, Subject"),
        ("Options Category", "Routine entry, Shift summary, Problem, Fix, Info, Other, Data analysis"),
        ("Preset Category", "Routine entry"),
        ("Extendable options", "Category"),
        ("Required Attributes", "Category"),
        ("Thread display", "$Subject (run $RunN), entered by $author on $Entry time"),
        ("Quick filter", "Date, RunN, Category, Sample, Subtext"),
        ("Page title", "ELOG - $subject - run: $RunN"),
        ("Entries per page", "50"),
    ],
    "Instrument": [
        ("Attributes", "Author, Category, Subject"),
        ("Options Category", "Hardware, Software, Routine entry, Problem, Fix, Maintenance, Info, Other"),
        ("Preset Category", "Routine entry"),
        ("Extendable options", "Category"),
        ("Required Attributes", "Category"),
        ("Thread display", "$Subject ($Category), entered by $author on $Entry time"),
        ("Quick filter", "Date, Category, Subtext"),
        ("Page title", "ELOG - $subject - $Category"),
    ],
    "Simple": [
        ("Attributes", "Author, Subject"),
    ],
}


def builtin(template: str) -> list[tuple[str, str]] | None:
    if template.startswith(PREFIX):
        return TEMPLATES.get(template[len(PREFIX):])
    return None

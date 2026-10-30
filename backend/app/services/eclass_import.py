"""Read three-term class records by their headings, not fixed cell addresses.

All three term sheets must contain a class-record table: learner lists,
Written Works, Performance Tasks, ST1/ST2/Term Examination and a highest
possible score row. Computed grades and summary/analysis tables are ignored.
Independently weighted domains (such as GMRC) become equivalent domain
points, with a preview warning, so the ordinary grading/save path retains
their weights without treating unlike domains as one pool of raw scores.
"""
from __future__ import annotations

import io
import math
import re
import zipfile
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Optional

MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS, MAX_COLS = 2000, 256


class NotAnEClassRecord(ValueError):
    """The file is not a readable three-term class record."""


@dataclass
class Learner:
    gender: str
    last_name: str
    first_name: str
    middle_name: Optional[str]
    written: str
    slot: int


@dataclass
class Term:
    term: int
    ww_highest: list
    pt_highest: list
    exam_highest: list
    weights: tuple
    scores: dict = field(default_factory=dict)
    exam_subweights: tuple = (30.0, 30.0, 40.0)


@dataclass
class EClassRecord:
    header: dict
    learners: list
    terms: list
    warnings: list


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return re.sub(r"\s+", " ", str(value)).strip()


def _label(value) -> str:
    return re.sub(r"[^a-z0-9]", "", _text(value).lower())


def _number(value) -> tuple[Optional[float], Optional[str]]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, None
    if isinstance(value, bool):
        return None, str(value)
    try:
        number = float(value)
    except (ValueError, TypeError):
        return None, _text(value)
    if not math.isfinite(number) or number < 0:
        return None, _text(value)
    return number, None


def _percent(value) -> Optional[float]:
    explicit = isinstance(value, str) and value.strip().endswith("%")
    number, _ = _number(value.strip().rstrip("%") if explicit else value)
    if number is None:
        return None
    return number if explicit or number > 1 else number * 100


def _proper(word: str) -> str:
    if word.isupper() or word.islower():
        return " ".join(part.capitalize() for part in word.split(" "))
    return word


def split_name(written: str) -> tuple[str, str, Optional[str]]:
    if "," in written:
        last, rest = written.split(",", 1)
        rest = re.sub(r"[,\s]+", " ", rest).strip()
    else:
        parts = written.rsplit(" ", 1)
        last, rest = (parts[1], parts[0]) if len(parts) == 2 else (written, "")
    last, rest = last.strip(), rest.strip()
    middle = None
    words = rest.split(" ")
    if len(words) > 1 and re.fullmatch(r"[A-Za-z]\.?", words[-1]):
        middle = words.pop().upper().rstrip(".") + "."
    return _proper(last), _proper(" ".join(words).strip()), middle


def name_key(last: str, first: str) -> str:
    return re.sub(r"[^a-z]", "", f"{last}|{first}".lower().replace("ñ", "n"))


def _valid_name(value) -> bool:
    last, first, _ = split_name(_text(value))
    return any(c.isalpha() for c in last) and any(c.isalpha() for c in first)


def _grid(sheet, max_row=MAX_ROWS, max_col=MAX_COLS) -> dict:
    """One bounded pass, including sheets with excessive formatted dimensions."""
    return {
        (r, c): value
        for r, row in enumerate(sheet.iter_rows(
            max_row=min(sheet.max_row or max_row, max_row),
            max_col=min(sheet.max_column or max_col, max_col), values_only=True,
        ), 1)
        for c, value in enumerate(row, 1) if value is not None
    }


_ORDINALS = {
    "1": 1, "1st": 1, "first": 1, "i": 1,
    "2": 2, "2nd": 2, "second": 2, "ii": 2,
    "3": 3, "3rd": 3, "third": 3, "iii": 3,
    "4": 4, "4th": 4, "fourth": 4, "iv": 4,
}


def _period(value, kinds=("term", "trimester", "trimestre")) -> Optional[int]:
    text = _label(value)
    for kind in kinds:
        match = re.fullmatch(rf"(?:{kind}(\d+)|(\d+)(?:st|nd|rd|th)?{kind})", text)
        if match:
            return int(match[1] or match[2])
        for ordinal, number in _ORDINALS.items():
            if text in (kind + ordinal, ordinal + kind):
                return number
    return None


def _mentioned_periods(value, kinds) -> set:
    """Find period labels with subject prefixes/suffixes as well as plain labels."""
    kind = "(?:" + "|".join(kinds) + ")"
    ordinal = r"(?:\d+(?:st|nd|rd|th)?|first|second|third|fourth|iv|iii|ii|i)"
    pattern = rf"\b{kind}\s*({ordinal})\b|\b({ordinal})\s*{kind}\b"
    numbers = set()
    for match in re.finditer(pattern, _text(value), re.I):
        token = (match[1] or match[2]).lower()
        number = _ORDINALS.get(token)
        if number is None:
            number = int(re.sub(r"(?:st|nd|rd|th)$", "", token))
        numbers.add(number)
    return numbers


def _term_sheets(grids, warnings) -> dict:
    terms = {}
    for title, cells in grids.items():
        header_values = [v for (r, _), v in cells.items() if r <= 40]
        period_values = (title, *header_values)
        if any(_mentioned_periods(v, ("quarter", "qtr", "q", "semester", "sem"))
               for v in period_values):
            raise NotAnEClassRecord("Only three-term class records are supported; quarter and semester records cannot be imported.")
        if any(any(n not in (1, 2, 3) for n in _mentioned_periods(v, ("term", "trimester", "trimestre")))
               for v in period_values):
            raise NotAnEClassRecord("Only three-term class records are supported; fourth-term records cannot be imported.")
        heading_terms = {_period(v) for v in header_values}
        heading_terms.discard(None)
        if any(n not in (1, 2, 3) for n in heading_terms):
            raise NotAnEClassRecord("Only three-term class records are supported; fourth or later terms cannot be imported.")
        number = _period(title)
        if number is not None and heading_terms and heading_terms != {number}:
            warnings.append(f'The heading on "{title}" names a different term; the sheet name is used. Check the copied heading.')
        if number is None and len(heading_terms) == 1:
            number = next(iter(heading_terms))
        if number is None:
            continue
        if number not in (1, 2, 3) or number in terms:
            raise NotAnEClassRecord("Include exactly one sheet each for Term 1, Term 2 and Term 3. Fourth or duplicate terms are not supported.")
        terms[number] = title
    if set(terms) != {1, 2, 3}:
        raise NotAnEClassRecord("Only three-term class records are supported. Include a class-record sheet for each of Term 1, Term 2 and Term 3, even if later terms have no scores yet.")
    return terms


def _category(value) -> Optional[str]:
    text = _label(value)
    if ("written" in text and "work" in text) or text in ("ww", "wws"):
        return "ww"
    if ("performance" in text and "task" in text) or text in ("pt", "pts"):
        return "pt"
    if "examination" in text or text in ("exam", "exams", "ex", "exs"):
        return "exam"
    return None


def _exam_item(value) -> Optional[int]:
    text = _label(value)
    if text in ("st1", "summativetest1", "summative1"):
        return 0
    if text in ("st2", "summativetest2", "summative2"):
        return 1
    if text in ("te", "termexam", "termexamination"):
        return 2
    return None


def _item_number(value) -> Optional[int]:
    match = re.fullmatch(r"(?:(?:ww|pt)\s*)?(\d+)", _text(value), re.I)
    return int(match[1]) if match and int(match[1]) > 0 else None


def _is_ws(value) -> bool:
    return _label(value) in ("ws", "weightedscore", "wsstte", "wsex", "wsexs")


@dataclass
class _Group:
    cols: list
    highest: list
    weight: Optional[float]


@dataclass
class _Layout:
    highest_row: int
    first_col: int
    groups: dict
    exam_cols: list
    exam_highest: list
    exam_subweights: tuple
    weights: tuple


def _layout(title, cells, warnings) -> _Layout:
    def invalid(reason):
        raise NotAnEClassRecord(f'"{title}" is not a readable three-term class record: {reason}.')

    # A secondary analysis panel can also say Highest Possible Score. Its
    # position and lack of the three assessment headings disqualify it.
    for highest_row, highest_col in sorted(k for k, v in cells.items() if _label(v) == "highestpossiblescore"):
        header_rows = {}
        for (r, c), value in cells.items():
            category = _category(value)
            if category and max(1, highest_row - 12) <= r < highest_row and c > highest_col:
                header_rows.setdefault(r, {})[category] = c
        complete = [(r, headers) for r, headers in header_rows.items() if set(headers) == {"ww", "pt", "exam"}]
        if complete:
            group_row, starts = max(complete)
            break
    else:
        invalid("the highest possible score row and Written Works, Performance Tasks and Examinations headings are required")
    if not starts["ww"] < starts["pt"] < starts["exam"]:
        invalid("the assessment headings overlap")

    derived_cols = [c for (r, c), v in cells.items() if r == group_row and c > starts["exam"]
                    and _label(v) in ("initialgrade", "termgrade", "descriptor")]
    end_col = min(derived_cols, default=max(c for _, c in cells) + 1)
    item_rows = []
    for row in range(group_row + 1, highest_row):
        exam = {_exam_item(v): c for (r, c), v in cells.items()
                if r == row and starts["exam"] <= c < end_col and _exam_item(v) is not None}
        if set(exam) == {0, 1, 2}:
            item_rows.append((row, exam))
    if not item_rows:
        invalid("ST1, ST2 and Term Examination columns are required")
    item_row, exam = item_rows[-1]

    def cap(col):
        value, problem = _number(cells.get((highest_row, col)))
        if problem:
            warnings.append(f'{title}: highest possible score "{problem}" ignored.')
        return value if value else None

    groups = {}
    for key, end in (("ww", starts["pt"]), ("pt", starts["exam"])):
        cols = [c for c in range(starts[key], end) if _item_number(cells.get((item_row, c))) is not None]
        if not cols or len(cols) > 40:
            invalid("each coursework component needs between 1 and 40 numbered assessment columns")
        runs = []
        for col in cols:
            if not runs or col != runs[-1][-1] + 1 or _item_number(cells[(item_row, col)]) == 1:
                runs.append([])
            runs[-1].append(col)
        groups[key] = []
        for index, run in enumerate(runs):
            next_col = runs[index + 1][0] if index + 1 < len(runs) else end
            weight_cols = [c for c in range(run[-1] + 1, next_col) if _is_ws(cells.get((item_row, c)))]
            weight = _percent(cells.get((highest_row, weight_cols[-1]))) if weight_cols else None
            if weight is None and len(runs) == 1:
                match = re.search(r"(\d+(?:\.\d+)?)\s*%", _text(cells.get((group_row, starts[key]))))
                weight = float(match[1]) if match else None
            if len(runs) > 1 and weight is None:
                invalid("each assessment domain must specify its weight")
            groups[key].append(_Group(run, [cap(c) for c in run], weight))

    exam_cols = [exam[i] for i in range(3)]
    exam_highest = [cap(c) for c in exam_cols]
    weight_cols = [c for c in range(max(exam_cols) + 1, end_col) if _is_ws(cells.get((item_row, c)))]
    exam_weight = _percent(cells.get((highest_row, weight_cols[-1]))) if weight_cols else None
    if exam_weight is None:
        match = re.search(r"(\d+(?:\.\d+)?)\s*%", _text(cells.get((group_row, starts["exam"]))))
        exam_weight = float(match[1]) if match else None
    weights = [sum(g.weight or 0 for g in groups[k]) if all(g.weight is not None for g in groups[k]) else default
               for k, default in (("ww", 20.0), ("pt", 50.0))]
    weights.append(30.0 if exam_weight is None else exam_weight)
    if abs(sum(weights) - 100) > .01:
        invalid("the component weights must total 100 percent")

    shares = {}
    for (r, c), value in cells.items():
        if r == item_row and max(exam_cols) < c < end_col:
            label = _label(value)
            for i, suffix in enumerate(("st1", "st2", "te")):
                if label.startswith("ws" + suffix):
                    share = _percent(cells.get((highest_row, c)))
                    if share is not None:
                        shares[i] = share
    if len(shares) == 3:
        subweights = tuple(shares[i] for i in range(3))
        if abs(sum(subweights) - 100) > .01:
            invalid("the examination shares must total 100 percent")
    elif any(_label(v).startswith("wsst1") for (r, c), v in cells.items() if r == item_row and c > max(exam_cols)):
        subweights = (30.0, 30.0, 40.0)
    else:
        # Older templates pool the three raw exams instead of assigning
        # fixed 30/30/40 shares. Proportional shares produce that same ratio.
        total = sum(h or 0 for h in exam_highest)
        subweights = tuple((h or 0) / total * 100 for h in exam_highest) if total else (30.0, 30.0, 40.0)
    return _Layout(highest_row, starts["ww"], groups, exam_cols,
                   exam_highest, subweights, tuple(weights))


_FIELDS = {
    "schoolid": "school_id", "schoolname": "school_name", "schoolyear": "school_year",
    "subjectteacher": "teacher", "teacher": "teacher", "nameofteacher": "teacher",
    "subject": "subject", "learningarea": "subject", "gradelevel": "grade_level",
    "grade": "grade_level", "section": "section", "gradesection": "grade_section",
}


def _header(cells) -> dict:
    header = {}
    for (r, c), value in cells.items():
        field_name = _FIELDS.get(_label(value))
        if field_name is None or r > 60:
            continue
        for next_col in range(c + 1, min(c + 16, MAX_COLS + 1)):
            next_value = _text(cells.get((r, next_col)))
            if not next_value or next_value == ":":
                continue
            if _label(next_value) in _FIELDS:
                break
            if next_value != "0":
                header.setdefault(field_name, next_value)
            break
    combined = header.pop("grade_section", "")
    if combined:
        match = re.match(r"^(?:GRADE\s*)?(\d+|VIII|VII|VI|IV|IX|III|II|V|X|I)\b[\s:-]*(.*)$", combined, re.I)
        if match:
            roman = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6,
                     "VII": 7, "VIII": 8, "IX": 9, "X": 10}
            header.setdefault("grade_level", str(roman.get(match[1].upper(), match[1])))
            if match[2]:
                header.setdefault("section", match[2])
        else:
            header.setdefault("section", combined)
    return header


@dataclass
class _RosterRow:
    gender: str
    written: str
    slot: int
    row: int
    col: int


def _roster(cells, resolve, *, after=0, before_col=MAX_COLS) -> list:
    markers = sorted((r, c, "Male" if _label(v) == "male" else "Female")
                     for (r, c), v in cells.items() if r > after and c < before_col
                     and _label(v) in ("male", "female"))
    out = {}
    max_row = max((r for r, _ in cells), default=0)
    for row, col, gender in markers:
        end = min((r for r, c, _ in markers if c == col and r > row), default=max_row + 1)
        candidates = []
        for name_col in (col, col + 1):
            names = []
            for r in range(row + 1, end):
                ordinal, _ = _number(cells.get((r, name_col - 1)))
                if ordinal is None or ordinal < 1 or not ordinal.is_integer():
                    continue
                written = _text(resolve(r, name_col))
                if _valid_name(written):
                    names.append(_RosterRow(gender, written, int(ordinal) - 1, r, name_col))
            candidates.append(names)
        for entry in max(candidates, key=len):
            out[(entry.row, entry.col)] = entry
    return sorted(out.values(), key=lambda entry: (entry.gender != "Male", entry.row, entry.col))


def read(content: bytes) -> EClassRecord:
    if len(content) > MAX_BYTES:
        raise NotAnEClassRecord("The file is larger than 5 MB.")
    if not zipfile.is_zipfile(io.BytesIO(content)):
        raise NotAnEClassRecord("Upload the three-term class record as an .xlsx file. Older .xls files must be saved as .xlsx in Excel first.")
    try:
        from openpyxl import load_workbook
        book = load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    except Exception as exc:
        raise NotAnEClassRecord("This file could not be opened as an Excel workbook.") from exc
    formulas = None
    try:
        grids = {sheet.title: _grid(sheet) for sheet in book}
        warnings = []
        term_sheets = _term_sheets(grids, warnings)
        layouts = {n: _layout(term_sheets[n], grids[term_sheets[n]], warnings) for n in (1, 2, 3)}
        input_sheets = [t for t in grids if _label(t) in ("input", "inputdata", "inputdatasheet")]
        formula_grids = {}

        def formula_cells(title):
            nonlocal formulas
            if formulas is None:
                formulas = load_workbook(io.BytesIO(content), data_only=False, read_only=True)
            if title not in formula_grids:
                formula_grids[title] = _grid(formulas[title])
            return formula_grids[title]

        def resolve(title, row, col):
            value = grids[title].get((row, col))
            if value is not None:
                return value
            # Excel formulas referencing INPUT often have no cached value in
            # freshly saved workbooks. Resolve only a single input-name cell;
            # arbitrary Excel formulas and computed scores are never evaluated.
            formula = _text(formula_cells(title).get((row, col)))
            reference = r"(?:'[^']+'|[A-Za-z_][\w ]*)!\$?[A-Z]+\$?\d+"
            if not (re.fullmatch(rf"={reference}", formula, re.I)
                    or re.fullmatch(rf'=IF\((?P<ref>{reference})="","",(?P=ref)\)', formula, re.I)):
                return None
            refs = re.findall(r"(?:'([^']+)'|([A-Za-z_][\w ]*))!\$?([A-Z]+)\$?(\d+)", formula)
            refs = {(a or b, column, int(r)) for a, b, column, r in refs}
            if len(refs) == 1:
                source, column, source_row = next(iter(refs))
                if source in input_sheets:
                    from openpyxl.utils import column_index_from_string
                    return grids[source].get((source_row, column_index_from_string(column)))
            return None

        term_rosters = {
            n: _roster(grids[title], lambda r, c, title=title: resolve(title, r, c),
                       after=layouts[n].highest_row, before_col=layouts[n].first_col)
            for n, title in term_sheets.items()
        }
        input_roster = []
        input_header = {}
        for title in input_sheets:
            input_header.update(_header(grids[title]))
            input_roster.extend(_roster(grids[title], lambda r, c, title=title: resolve(title, r, c)))
        first_roster = term_rosters[1]
        key_of = lambda entry: name_key(*split_name(entry.written)[:2])
        first_keys = {key_of(e) for e in first_roster}
        overlap = sum(key_of(e) in first_keys for e in input_roster)
        primary = input_roster if input_roster and (not first_roster or overlap >= .8 * max(len(input_roster), len(first_roster))) else first_roster
        if not primary:
            primary = next((term_rosters[n] for n in (2, 3) if term_rosters[n]), [])
        if not primary:
            raise NotAnEClassRecord("No readable learners' names were found in the input or term sheets. Names must be in numbered male/female learner lists.")

        header = dict(input_header)
        for n in (1, 2, 3):
            term_header = _header(grids[term_sheets[n]])
            if n == 1:
                for key, value in term_header.items():
                    if header.get(key) and _label(header[key]) != _label(value):
                        warnings.append(f'The input and Term 1 {key.replace("_", " ")} differ; "{value}" from Term 1 is used.')
                    header[key] = value
            else:
                for key, value in term_header.items():
                    if header.get(key) and _label(header[key]) != _label(value):
                        warnings.append(f'Term {n} has a different {key.replace("_", " ")}: "{value}". Check that all terms belong to the selected class.')
                    header.setdefault(key, value)
        header = {key: header.get(key, "") for key in ("school_id", "school_name", "school_year", "teacher", "subject", "grade_level", "section")}
        first_by_key = {key_of(e): e for e in first_roster}
        learners, lookup = [], {}

        def add(entry, *, allow_alias=False):
            key = key_of(entry)
            if key in lookup:
                return lookup[key]
            last, first, middle = split_name(entry.written)
            # A common spelling change across terms should not create a new
            # learner. Only a unique near-identical name with the same surname
            # and numbered slot qualifies, and the preview reports the match.
            candidates = [i for i, learner in enumerate(learners)
                          if allow_alias and name_key(learner.last_name, "") == name_key(last, "")
                          and learner.slot == entry.slot
                          and learner.gender == entry.gender
                          and SequenceMatcher(None, _label(learner.first_name), _label(first)).ratio() >= .8]
            if len(candidates) == 1:
                index = candidates[0]
                warnings.append(f'Name "{entry.written}" was matched to "{learners[index].written}" in the same numbered slot; check the spelling.')
            else:
                canonical = first_by_key.get(key, entry)
                index = len(learners)
                learners.append(Learner(canonical.gender, last, first, middle, entry.written, canonical.slot))
            lookup[key] = index
            return index

        for entry in primary:
            add(entry)
        terms = []
        for n in (1, 2, 3):
            title, layout = term_sheets[n], layouts[n]
            roster = term_rosters[n]
            if not roster:
                raise NotAnEClassRecord(f'"{title}" has no readable numbered learner list.')
            indexes = {}
            for entry in roster:
                index = add(entry, allow_alias=True)
                if index in indexes:
                    raise NotAnEClassRecord(f'"{title}" lists the same learner more than once. Resolve duplicate names before importing.')
                indexes[index] = entry.row
            score_cols = layout.exam_cols + [c for groups in layout.groups.values() for group in groups for c in group.cols]
            source = formula_cells(title)
            uncached = sum(grids[title].get((r, c)) is None and _text(source.get((r, c))).startswith("=")
                           for r in indexes.values() for c in score_cols)
            if uncached:
                warnings.append(f'Term {n}: {uncached} score formula(s) have no saved result and were read as blank. Recalculate and save the workbook in Excel before importing those scores.')
            term = _read_term(grids[title], n, layout, indexes, learners, warnings)
            if term is not None:
                terms.append(term)
        return EClassRecord(header, learners, terms, list(dict.fromkeys(warnings)))
    except NotAnEClassRecord:
        raise
    except Exception as exc:
        raise NotAnEClassRecord("The workbook could not be read as a three-term class record. Check its learner lists and assessment headings.") from exc
    finally:
        book.close()
        if formulas is not None:
            formulas.close()


def _read_term(cells, number, layout, indexes, learners, warnings) -> Optional[Term]:
    label = f"Term {number}"
    odd_marks, over = set(), set()
    normalized = [key for key, groups in layout.groups.items() if len(groups) > 1]

    def highest(key):
        groups = layout.groups[key]
        values = ([g.weight for g in groups]
                  if len(groups) > 1 else list(groups[0].highest))
        while values and values[-1] is None:
            values.pop()
        return values

    ww_highest, pt_highest = highest("ww"), highest("pt")

    def raw(row, cols, caps, index):
        values = []
        for col, cap in zip(cols, caps):
            value, problem = _number(cells.get((row, col)))
            if problem:
                odd_marks.add(problem)
            if value is not None and cap is None:
                warnings.append(f"{label}: {learners[index].written} has a score in a column with no highest possible score; ignored.")
                value = None
            if value is not None and cap is not None and value > cap:
                over.add(learners[index].written)
            if value is not None and value > 1000:
                raise NotAnEClassRecord(f"{label}: scores above 1000 cannot be imported.")
            values.append(value)
        return values

    scores = {}
    for index, row in indexes.items():
        got = {}
        for key, caps in (("ww", ww_highest), ("pt", pt_highest)):
            groups = layout.groups[key]
            values = []
            for group in groups:
                source = raw(row, group.cols, group.highest, index)
                if len(groups) == 1:
                    values.extend(source)
                else:
                    total = sum(h or 0 for h in group.highest)
                    values.append(sum(v or 0 for v in source) / total * group.weight
                                  if total and any(v is not None for v in source) else None)
            got[key] = values[:len(caps)]
        got["exam"] = raw(row, layout.exam_cols, layout.exam_highest, index)
        if any(v is not None for values in got.values() for v in values):
            scores[index] = got
    if not scores:
        return None
    if normalized:
        warnings.append(f'{label}: independently weighted {"/".join(k.upper() for k in normalized)} domains were converted to equivalent domain points to preserve their weights. Each imported assessment represents one domain.')
    if odd_marks:
        shown = ", ".join(f'"{m}"' for m in sorted(odd_marks)[:5])
        warnings.append(f"{label}: cells marked {shown} were read as blank (no score).")
    if over:
        names = ", ".join(sorted(over)[:3])
        warnings.append(f"{label}: scores above the highest possible score for {names}" + (f" and {len(over) - 3} more" if len(over) > 3 else "") + ".")
    return Term(number, ww_highest, pt_highest, layout.exam_highest,
                layout.weights, scores, layout.exam_subweights)

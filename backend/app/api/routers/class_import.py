"""Import a DepEd 3-Term E-Class Record into one section's class record.

One endpoint, two steps. With ``save`` false it only reads the file and says
what would happen: who matches an enrolled learner, who is new, what each
term holds. With ``save`` true it does it: new learners are added and
enrolled in the section, each term's header (highest possible scores and
weights) is written, and the scores go through the same save as a typed
class record, so grades, early warnings and the AI forecast follow exactly
as they would after encoding by hand.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, school_scope
from app.api.routers.pace import (
    _load_setup,
    _recompute_grades,
    _score_batch,
    save_class_rows,
)
from app.api.scoping import get_scoped, scoped
from app.db.session import get_db
from app.models.academic import GradeLevel, SchoolYear, Section, Subject
from app.models.schools import School
from app.models.staff import Staff
from app.models.students import (
    ClassRecordSetup,
    Student,
    StudentEnrollment,
    StudentPace,
)
from app.schemas.students import BulkPaceEntry, BulkPaceRequest
from app.services import eclass_import, grading
from app.services.access import class_access
from app.services.audit import Action, log_event

router = APIRouter(tags=["Class record import"])

# What happens to each learner in the file.
MATCHED = "matched"          # already enrolled in this section
ENROL = "enrol"              # in the school, not enrolled this year: enrolled here
NEW = "new"                  # not in the school: added and enrolled here
ELSEWHERE = "elsewhere"      # enrolled in another section this year: skipped


def _same(a: str, b: str) -> bool:
    return re.sub(r"[^a-z0-9]", "", (a or "").lower()) == re.sub(
        r"[^a-z0-9]", "", (b or "").lower()
    )


async def _current_year(db: AsyncSession, scope: int) -> Optional[SchoolYear]:
    year = (
        await db.execute(
            scoped(select(SchoolYear).where(SchoolYear.is_current.is_(True)),
                   SchoolYear, scope)
        )
    ).scalar_one_or_none()
    if year is None:
        year = (
            await db.execute(
                scoped(select(SchoolYear).order_by(SchoolYear.start_date.desc()).limit(1),
                       SchoolYear, scope)
            )
        ).scalar_one_or_none()
    return year


@router.post("/api/class-record/import")
async def import_class_record(
    request: Request,
    file: UploadFile = File(...),
    section_id: int = Form(...),
    subject_id: int = Form(...),
    save: bool = Form(False),
    current: Staff = Depends(get_current_user),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    school = await db.get(School, scope)
    if school is not None and school.tracks_units:
        raise HTTPException(
            status_code=400,
            detail="Class record import is for schools that use the DepEd class record.",
        )

    section = await get_scoped(db, Section, section_id, scope, "Section")
    subject = await get_scoped(db, Subject, subject_id, scope, "Subject")
    if subject.grade_level_id != section.grade_level_id:
        raise HTTPException(status_code=400,
                            detail="That subject is not taught in this section's grade level.")
    # Only into a class this teacher grades.
    (await class_access(db, current, scope)).require_grade(section_id, subject.subject_name)

    content = await file.read(eclass_import.MAX_BYTES + 1)
    try:
        record = eclass_import.read(content)
    except eclass_import.NotAnEClassRecord as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    level = await db.get(GradeLevel, section.grade_level_id)
    year = await _current_year(db, scope)
    if year is None:
        raise HTTPException(status_code=400,
                            detail="Add a school year in Class Management first.")

    # ── Does the file belong to this class? Said, not refused: names on a
    # header are often typed loosely, and the teacher picked the class.
    head = record.header
    notes = list(record.warnings)
    if head["subject"] and not _same(head["subject"], subject.subject_name):
        notes.insert(0, f'The file is for "{head["subject"]}"; it will go into '
                        f"{subject.subject_name}.")
    if head["grade_level"] and level and not re.search(
        rf"\b{re.escape(head['grade_level'])}\b", f"{level.level} {level.name}"
    ):
        notes.insert(0, f"The file is for Grade {head['grade_level']}; this section is "
                        f"{level.name}.")
    if head["section"] and not (
        _same(head["section"], section.name)
        or head["section"].lower() in section.name.lower()
        or _same(head["section"], section.section_code)
    ):
        notes.insert(0, f'The file is for section "{head["section"]}"; it will go into '
                        f"{section.name}.")
    if head["school_year"] and not _same(head["school_year"], year.year):
        notes.insert(0, f"The file is for S.Y. {head['school_year']}; the current school "
                        f"year is {year.year}.")

    # ── Who is who.
    enrolled = (
        await db.execute(
            scoped(
                select(StudentEnrollment, Student)
                .join(Student, Student.student_id == StudentEnrollment.student_id)
                .where(StudentEnrollment.school_year_id == year.school_year_id,
                       StudentEnrollment.is_active.is_(True)),
                StudentEnrollment,
                scope,
            )
        )
    ).all()
    in_section = {}
    in_other = {}
    for enrolment, student in enrolled:
        key = eclass_import.name_key(student.last_name, student.first_name)
        if enrolment.section_id == section_id:
            in_section[key] = (student, enrolment)
        else:
            in_other[key] = enrolment.section_id
    others = {
        s.section_id: s.name
        for s in (await db.execute(scoped(select(Section), Section, scope))).scalars()
    }
    school_students = {
        eclass_import.name_key(s.last_name, s.first_name): s
        for s in (
            await db.execute(
                scoped(select(Student).where(Student.is_active.is_(True)), Student, scope)
            )
        ).scalars()
    }

    plan = []
    for learner in record.learners:
        key = eclass_import.name_key(learner.last_name, learner.first_name)
        if key in in_section:
            plan.append((MATCHED, learner, *in_section[key]))
        elif key in in_other:
            plan.append((ELSEWHERE, learner, None, None))
        elif key in school_students:
            plan.append((ENROL, learner, school_students[key], None))
        else:
            plan.append((NEW, learner, None, None))

    skipped = [p for p in plan if p[0] == ELSEWHERE]
    for _, learner, _, _ in skipped:
        key = eclass_import.name_key(learner.last_name, learner.first_name)
        notes.append(f"{learner.written} is enrolled in {others.get(in_other[key], 'another section')}"
                     "; skipped.")

    joining = sum(1 for p in plan if p[0] in (NEW, ENROL))
    if joining:
        taken = (
            await db.execute(
                select(func.count()).select_from(StudentEnrollment).where(
                    StudentEnrollment.school_id == scope,
                    StudentEnrollment.section_id == section_id,
                    StudentEnrollment.school_year_id == year.school_year_id,
                    StudentEnrollment.is_active.is_(True),
                )
            )
        ).scalar_one()
        if taken + joining > section.capacity:
            notes.append(f"{section.name} will have {taken + joining} learners, above its "
                         f"capacity of {section.capacity}.")

    def term_summary(term):
        complete = 0
        for scores in term.scores.values():
            result = grading.compute(
                ww_scores=scores["ww"], pt_scores=scores["pt"], exam_scores=scores["exam"],
                ww_highest=term.ww_highest, pt_highest=term.pt_highest,
                exam_highest=term.exam_highest, ww_weight=term.weights[0],
                pt_weight=term.weights[1], ex_weight=term.weights[2],
            )
            complete += bool(result["complete"])
        return {
            "term": term.term,
            "learners": len(term.scores),
            "graded": complete,
            "written_works": len(term.ww_highest),
            "performance_tasks": len(term.pt_highest),
            "exams": sum(1 for h in term.exam_highest if h),
            "weights": list(term.weights),
        }

    preview = {
        "file": {k: v for k, v in head.items() if v},
        "section": section.name,
        "subject": subject.subject_name,
        "learners": [
            {
                "name": f"{l.last_name}, {l.first_name}" + (f" {l.middle_name}" if l.middle_name else ""),
                "gender": l.gender,
                "status": status,
            }
            for status, l, _, _ in plan
        ],
        "counts": {
            MATCHED: sum(1 for p in plan if p[0] == MATCHED),
            NEW: sum(1 for p in plan if p[0] == NEW),
            ENROL: sum(1 for p in plan if p[0] == ENROL),
            ELSEWHERE: len(skipped),
        },
        "terms": [term_summary(t) for t in record.terms],
        "warnings": notes,
    }
    if not record.terms:
        preview["warnings"].append("The file has names but no scores yet.")
    if not save:
        return preview

    # ── Save. Learners first, so every row below has an enrolment.
    enrolment_of: dict[int, tuple[int, int]] = {}   # learner index -> (student, enrolment)
    created = enrolled_now = 0
    for index, (status, learner, student, enrolment) in enumerate(plan):
        if status == ELSEWHERE:
            continue
        if status == NEW:
            student = Student(
                school_id=scope,
                first_name=learner.first_name[:50],
                middle_name=(learner.middle_name or None),
                last_name=learner.last_name[:50],
                gender=learner.gender,
                created_by=current.id,
            )
            db.add(student)
            await db.flush()
            student.login_id = f"S{student.student_id:04d}"
            created += 1
        if status in (NEW, ENROL):
            enrolment = StudentEnrollment(
                school_id=scope,
                student_id=student.student_id,
                grade_level_id=section.grade_level_id,
                section_id=section_id,
                school_year_id=year.school_year_id,
                enrolled_by=current.id,
                enrollment_date=date.today(),
                is_active=True,
            )
            db.add(enrolment)
            await db.flush()
            # Learners already in the school, now enrolled here. New ones
            # are counted under "added".
            enrolled_now += status == ENROL
        enrolment_of[index] = (student.student_id, enrolment.enrollment_id)

    # Each term: the header, then the scores through the ordinary save.
    saved_terms = []
    touched = []
    for term in record.terms:
        setup = (
            await db.execute(
                scoped(
                    select(ClassRecordSetup).where(
                        ClassRecordSetup.section_id == section_id,
                        ClassRecordSetup.subject_id == subject_id,
                        ClassRecordSetup.grading_period == term.term,
                    ),
                    ClassRecordSetup,
                    scope,
                )
            )
        ).scalar_one_or_none()
        if setup is None:
            setup = ClassRecordSetup(school_id=scope, section_id=section_id,
                                     subject_id=subject_id, grading_period=term.term,
                                     template=grading.CORE)
            db.add(setup)
        setup.ww_highest = json.dumps(term.ww_highest)
        setup.pt_highest = json.dumps(term.pt_highest)
        setup.exam_highest = json.dumps(term.exam_highest)
        setup.ww_weight, setup.pt_weight, setup.ex_weight = term.weights
        setup.updated_by = current.id
        await db.flush()

        # Records already in this term that the file does not touch are
        # regraded against the new header too, as a header save does.
        header = await _load_setup(db, scope, section_id, subject_id, term.term)
        existing = (
            await db.execute(
                scoped(
                    select(StudentPace)
                    .join(StudentEnrollment,
                          StudentEnrollment.enrollment_id == StudentPace.enrollment_id)
                    .where(StudentEnrollment.section_id == section_id,
                           StudentPace.subject == subject.subject_name,
                           StudentPace.grading_period == term.term),
                    StudentPace,
                    scope,
                )
            )
        ).scalars().all()
        for pace in existing:
            _recompute_grades(pace, header)

        entries = [
            BulkPaceEntry(
                student_id=enrolment_of[index][0],
                enrollment_id=enrolment_of[index][1],
                subject=subject.subject_name,
                current_pace=0,
                expected_pace=0,
                total_paces=0,
                ww_scores=scores["ww"],
                pt_scores=scores["pt"],
                exam_scores=scores["exam"],
            )
            for index, scores in term.scores.items()
            if index in enrolment_of
        ]
        if not entries:
            continue
        result = await save_class_rows(
            BulkPaceRequest(entries=entries, section_id=section_id,
                            subject_id=subject_id, grading_period=term.term),
            request, current, scope, db, score=False,
        )
        saved_terms.append({"term": term.term, "saved": result["saved"],
                            "skipped": result["skipped"]})
        touched.extend(result["rows"])

    # The forecast reads each learner's first term, whichever terms came in,
    # so it is asked for once for the whole file.
    ai = await _score_batch(db, touched, scope) if touched else None

    await log_event(
        db, Action.PACE_RECORDED, actor_id=current.id,
        detail=(f"class record imported into section {section_id} subject {subject_id}: "
                f"{created} learners added, {enrolled_now} enrolled, "
                f"terms {[t['term'] for t in saved_terms]}"),
        request=request,
    )
    await db.commit()
    return {
        **preview,
        "saved": {
            "added": created,
            "enrolled": enrolled_now,
            "skipped": len(skipped),
            "terms": saved_terms,
            "ai": ai,
        },
    }

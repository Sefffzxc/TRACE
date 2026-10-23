"""Cross-check class-boundary findings through real JWT authentication.

The only fixtures are invented records in a new audit-owned database. No
FastAPI dependency overrides or production database access are used.
"""
import asyncio
from datetime import date
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

os.environ["SECRET_KEY"]=secrets.token_urlsafe(48)
sys.path.insert(0,str(Path(__file__).parent))
import probe_backend_data_ai as b
from app.core.security import create_access_token
from app.models.students import EarlyWarning

async def main():
    b.app.dependency_overrides.clear()
    await b.init_models()
    async with b.AsyncSessionLocal() as db:
        s=b.School(name="Crosscheck school",status="approved");db.add(s);await db.flush()
        t=b.Staff(school_id=s.school_id,email="crosscheck-teacher@audit.example",password_hash="fixture-only",first_name="Crosscheck",last_name="Teacher",role="teacher",contact_number="0",is_approved=True,account_status="approved")
        admin=b.Staff(school_id=s.school_id,email="crosscheck-admin@audit.example",password_hash="fixture-only",first_name="Crosscheck",last_name="Admin",role="admin",contact_number="0",is_approved=True,account_status="approved")
        g=b.GradeLevel(school_id=s.school_id,level="7",name="Grade 7");y=b.SchoolYear(school_id=s.school_id,year="2026-2027",start_date=date(2026,6,1),end_date=date(2027,5,31),is_current=True)
        db.add_all([t,admin,g,y]);await db.flush()
        sec=b.Section(school_id=s.school_id,grade_level_id=g.grade_level_id,section_code="OWN",name="Own");othersec=b.Section(school_id=s.school_id,grade_level_id=g.grade_level_id,section_code="PRIVATE",name="Private")
        math=b.Subject(school_id=s.school_id,grade_level_id=g.grade_level_id,subject_code="MATH",subject_name="Mathematics");science=b.Subject(school_id=s.school_id,grade_level_id=g.grade_level_id,subject_code="SCI",subject_name="Science");history=b.Subject(school_id=s.school_id,grade_level_id=g.grade_level_id,subject_code="HIST",subject_name="History")
        children=[b.Student(school_id=s.school_id,first_name=n,last_name="Synthetic") for n in ["Owned","Private"]]
        db.add_all([sec,othersec,math,science,history,*children]);await db.flush()
        db.add(b.TeacherAssignment(school_id=s.school_id,teacher_id=t.id,section_id=sec.section_id,subject_id=math.subject_id))
        enrollments=[b.StudentEnrollment(school_id=s.school_id,student_id=child.student_id,grade_level_id=g.grade_level_id,section_id=sect.section_id,school_year_id=y.school_year_id,is_active=True) for child,sect in zip(children,[sec,othersec])]
        db.add_all(enrollments);await db.flush()
        paces=[b.StudentPace(school_id=s.school_id,student_id=children[0].student_id,enrollment_id=enrollments[0].enrollment_id,subject=sub,grading_period=1,transmuted_grade=grade,record_complete=True) for sub,grade in [("Mathematics",100),("Science",60)]]
        db.add_all(paces)
        db.add(b.DataQualityLog(school_id=s.school_id,student_id=children[1].student_id,issue_type="private",detail="Synthetic private case"))
        await db.commit()
    token=create_access_token({"sub":str(t.id)})
    headers={"Authorization":"Bearer "+token}
    results={"dependency_override_count":len(b.app.dependency_overrides)}
    async with b.httpx.AsyncClient(transport=b.httpx.ASGITransport(app=b.app,raise_app_exceptions=False),base_url="http://audit-crosscheck") as c:
        results["unauthenticated_students_status"]=(await c.get("/api/students")).status_code
        results["unassigned_student_status"]=(await c.get(f"/api/students/{children[1].student_id}",headers=headers)).status_code
        results["teacher_admin_write_status"]=(await c.post("/api/grade-levels",json={"level":"8","name":"Grade 8"},headers=headers)).status_code
        normal=await c.get(f"/api/students/{children[0].student_id}",headers=headers)
        changed=await c.patch(f"/api/students/{children[0].student_id}",json={"address":"Synthetic update"},headers=headers)
        results["BD04_read_patch"]={"get_status":normal.status_code,"get_lowest_grade":normal.json().get("lowest_term_grade"),"patch_status":changed.status_code,"patch_lowest_grade":changed.json().get("lowest_term_grade"),"patch_weakest_subject":changed.json().get("weakest_subject")}
        # Persist the cross-runtime pass/fail boundary through the real HTTP
        # bulk-save route and then read it back through a separate request.
        parity_setup={"template":"core","ww_highest":[100],"pt_highest":[100],"exam_highest":[100,100,100],"ww_weight":20,"pt_weight":50,"ex_weight":30,"exam_subweights":[30,30,40]}
        parity_scores={"ww_scores":[24.625],"pt_scores":[100],"exam_scores":[50.25,50.25,50.25]}
        setup_saved=await c.put(f"/api/class-record/setup?section_id={sec.section_id}&subject_id={math.subject_id}&grading_period=1",json=parity_setup,headers=headers)
        saved=await c.post("/api/paces/bulk",json={"section_id":sec.section_id,"subject_id":math.subject_id,"grading_period":1,"entries":[{"student_id":children[0].student_id,"enrollment_id":enrollments[0].enrollment_id,"subject":"Mathematics","current_pace":0,"expected_pace":0,"total_paces":0,**parity_scores}]},headers=headers)
        readback=await c.get(f"/api/paces?enrollment_id={enrollments[0].enrollment_id}&subject=Mathematics",headers=headers)
        actual=readback.json()[0]
        js="import fs from 'node:fs';import {computeRow} from './frontend/src/lib/grading.js';const x=JSON.parse(fs.readFileSync(0,'utf8'));process.stdout.write(JSON.stringify(computeRow(x.row,x.setup)));"
        preview=json.loads(subprocess.run(["node","--input-type=module","-e",js],cwd=b.ROOT,input=json.dumps({"row":parity_scores,"setup":parity_setup}),text=True,capture_output=True,check=True).stdout)
        async with b.AsyncSessionLocal() as db:
            persisted=await db.get(b.StudentPace,actual["pace_id"])
            persisted_grade=persisted.transmuted_grade
        results["BF03_persisted_grading_parity"]={"setup_status":setup_saved.status_code,"save_status":saved.status_code,"readback_status":readback.status_code,"fixture":{"row":parity_scores,"setup":parity_setup},"frontend":{"ww_total":preview["ww_total"],"initial_grade":preview["initial_grade"],"term_grade":preview["transmuted_grade"],"descriptor":preview["descriptor"],"complete":preview["complete"]},"backend":{"ww_total":actual["ww_total_raw"],"initial_grade":actual["initial_grade"],"term_grade":actual["transmuted_grade"],"descriptor":actual["descriptor"],"complete":actual["record_complete"]},"database_persisted_term_grade":persisted_grade}
        renamed=await c.patch(f"/api/paces/{paces[0].pace_id}",json={"subject":"History"},headers=headers)
        results["BD01_unassigned_subject_rename"]={"status":renamed.status_code,"subject":renamed.json().get("subject")}
        mismatch=await c.post("/api/paces",json={"student_id":children[1].student_id,"enrollment_id":enrollments[0].enrollment_id,"subject":"Mathematics"},headers=headers)
        results["BD02_mismatch"]={"status":mismatch.status_code,"student_id":mismatch.json().get("student_id"),"enrollment_id":mismatch.json().get("enrollment_id")}
        q=await c.get("/api/data-quality-logs",headers=headers)
        results["BD03_quality_private_case"]={"status":q.status_code,"unassigned_student_case_visible":any(r["student_id"]==children[1].student_id for r in q.json())}
        poisoned=await c.patch(f"/api/students/{children[0].student_id}",json={"gender":"invalid"},headers=headers)
        read=await c.get(f"/api/students/{children[0].student_id}",headers=headers)
        results["BD05_persistent_invalid_profile"]={"patch_status":poisoned.status_code,"subsequent_read_status":read.status_code}
    b.app.dependency_overrides.clear()
    await b.async_engine.dispose()
    (b.ROOT/"audit/backend-jwt-crosscheck.json").write_text(json.dumps(results,indent=2),encoding="utf-8")
    print(json.dumps(results,indent=2))

asyncio.run(main())

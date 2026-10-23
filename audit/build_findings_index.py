"""Generate a review index without treating duplicate/cross-layer entries as unique vulnerabilities."""
import csv
import pathlib
import re

root=pathlib.Path(__file__).resolve().parent
reports=['security-auth.md','backend-data-ai.md','backend-followup.md','frontend-flows.md','browser-runtime.md','python-dependencies.md','frontend-dependencies.md']
rows=[]
for report in reports:
    for line_number,line in enumerate((root/report).read_text(encoding='utf-8').splitlines(),1):
        match=re.match(r'^#{2,3}\s+((?:SA|BD|BF|FE|RT|DEP|ND)-\d+)\s+[—-]\s+(.+)$',line)
        if not match:
            continue
        ident,description=match.groups()
        severity=re.search(r'\b(Critical|High|Medium|Low)\b',description)
        rows.append({'id':ident,'severity':severity.group(1) if severity else 'Review','finding':description,'status':'Open','report':report,'heading_line':line_number})
with (root/'findings-index.csv').open('w',newline='',encoding='utf-8-sig') as stream:
    writer=csv.DictWriter(stream,fieldnames=['id','severity','finding','status','report','heading_line'])
    writer.writeheader()
    writer.writerows(rows)
print('Indexed report entries:',len(rows))
print('Entries by severity:',{level:sum(row['severity']==level for row in rows) for level in ['Critical','High','Medium','Low','Review']})
print('Entries can overlap and include maintenance/configuration-dependent findings; do not label this a count of unique exploitable vulnerabilities.')

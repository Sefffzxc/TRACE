import { useState } from 'react';
import { AlertTriangle, FileSpreadsheet, Upload } from 'lucide-react';
import { Modal } from './ui';
import { classRecordApi } from '../../api/endpoints';
import { periodName } from '../../lib/grading';

/* Brings a DepEd 3-Term E-Class Record (.xlsx) into the class record open on
   screen. The file is read first and nothing is saved until the teacher has
   seen who matches, who is new and what each term holds. */

const STATUS = {
  matched: { label: 'In this class', badge: 'badge-low' },
  new: { label: 'New, will be added', badge: 'badge-info' },
  enrol: { label: 'Will be enrolled', badge: 'badge-info' },
  elsewhere: { label: 'Other section, skipped', badge: 'badge-moderate' },
};

export default function ImportClassRecord({ section, subject, periodLabel, onClose, onDone }) {
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');

  const target = { section_id: section.section_id, subject_id: subject.subject_id };

  const choose = async (event) => {
    const picked = event.target.files?.[0];
    event.target.value = '';
    if (!picked) return;
    setFile(picked);
    setPreview(null);
    setError('');
    setBusy('reading');
    try {
      setPreview(await classRecordApi.importFile(picked, target));
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy('');
    }
  };

  const save = async () => {
    setBusy('saving');
    setError('');
    try {
      const result = await classRecordApi.importFile(file, { ...target, save: true });
      onDone(result.saved);
    } catch (err) {
      setError(err.message);
      setBusy('');
    }
  };

  const counts = preview?.counts || {};
  const joining = (counts.new || 0) + (counts.enrol || 0);
  const nothing = preview && !preview.terms.length && !joining;

  return (
    <Modal
      title="Import class record"
      subtitle={`${section.name} · ${subject.subject_name}`}
      size="lg"
      onClose={busy === 'saving' ? undefined : onClose}
      footer={
        <>
          <button type="button" className="btn btn-secondary" onClick={onClose} disabled={busy === 'saving'}>
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-primary"
            onClick={save}
            disabled={!preview || nothing || Boolean(busy)}
          >
            <Upload size={15} /> {busy === 'saving' ? 'Importing…' : 'Import'}
          </button>
        </>
      }
    >
      <div className="stack">
        <label className="import-drop">
          <FileSpreadsheet size={22} />
          <span>
            <strong>{file ? file.name : 'Choose the E-Class Record file'}</strong>
            <span className="faint">
              {busy === 'reading' ? 'Reading…' : 'DepEd 3-Term E-Class Record, .xlsx'}
            </span>
          </span>
          <input type="file" accept=".xlsx" onChange={choose} disabled={Boolean(busy)} />
        </label>

        {error && (
          <div className="alert alert-warning">
            <AlertTriangle size={17} />
            <span>{error}</span>
          </div>
        )}

        {preview && (
          <>
            <div className="import-counts">
              <span><strong>{preview.learners.length}</strong> learners in the file</span>
              <span><strong>{counts.matched || 0}</strong> already in this class</span>
              <span><strong>{joining}</strong> to add</span>
              {counts.elsewhere > 0 && <span><strong>{counts.elsewhere}</strong> skipped</span>}
            </div>

            {preview.warnings.length > 0 && (
              <div className="alert alert-warning">
                <AlertTriangle size={17} />
                <ul className="import-notes">
                  {preview.warnings.map((note) => (
                    <li key={note}>{note}</li>
                  ))}
                </ul>
              </div>
            )}

            {preview.terms.length > 0 && (
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>{periodLabel}</th>
                      <th>Learners</th>
                      <th>Graded</th>
                      <th>Items (WW · PT · Exams)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {preview.terms.map((t) => (
                      <tr key={t.term}>
                        <td className="cell-strong">{periodName(t.term, periodLabel)}</td>
                        <td>{t.learners}</td>
                        <td>
                          {t.graded === t.learners ? (
                            t.graded
                          ) : (
                            <span title="A grade shows once every item, including the exams, has a score.">
                              {t.graded} <span className="faint">· in progress</span>
                            </span>
                          )}
                        </td>
                        <td>
                          {t.written_works} · {t.performance_tasks} · {t.exams}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            <details className="import-learners">
              <summary>Learners</summary>
              <ul>
                {preview.learners.map((l) => (
                  <li key={`${l.gender}-${l.name}`}>
                    <span>{l.name}</span>
                    <span className={`badge ${STATUS[l.status].badge}`}>{STATUS[l.status].label}</span>
                  </li>
                ))}
              </ul>
            </details>

            {joining > 0 && (
              <p className="faint" style={{ margin: 0 }}>
                New learners are added with their names only. Their profiles show as
                incomplete until the rest is filled in.
              </p>
            )}
          </>
        )}
      </div>
    </Modal>
  );
}

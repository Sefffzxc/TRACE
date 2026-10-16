import { useEffect, useId, useMemo, useState } from 'react';

/* A school's address, picked from the PSA's list instead of typed out:
   province, then a city or municipality in that province, then a barangay in
   that city. Typing filters each list; anything not listed can still be typed
   in. The lists are built by scripts/build-places.mjs.

   The school keeps four fields — address, city, province, region — so the
   barangay and street are joined into `address`, and the region is filled in
   from the province. */

let provincesLoad = null;
const barangayLoads = {};

function loadProvinces() {
  provincesLoad ??= fetch('/places/provinces.json')
    .then((res) => (res.ok ? res.json() : []))
    .catch(() => []);
  return provincesLoad;
}

function loadBarangays(provinceCode) {
  barangayLoads[provinceCode] ??= fetch(`/places/barangays/${provinceCode}.json`)
    .then((res) => (res.ok ? res.json() : {}))
    .catch(() => ({}));
  return barangayLoads[provinceCode];
}

const same = (a, b) => a.trim().toLowerCase() === b.trim().toLowerCase();

// "Purok 3, Poblacion" -> street "Purok 3", barangay "Poblacion".
function splitAddress(address) {
  const text = address || '';
  const cut = text.lastIndexOf(', ');
  return cut < 0 ? { street: text, barangay: '' } : { street: text.slice(0, cut), barangay: text.slice(cut + 2) };
}

const joinAddress = (street, barangay) =>
  [street.trim(), barangay.trim()].filter(Boolean).join(', ');

export default function AddressFields({ value, onChange, idPrefix = 'addr' }) {
  const [provinces, setProvinces] = useState([]);
  const [barangays, setBarangays] = useState({});
  const [{ street, barangay }, setParts] = useState(() => splitAddress(value.address));

  // The saved address can arrive after the form opens, or be reset by the
  // page; take it up whenever it is not the one these boxes last sent.
  const [sent, setSent] = useState(value.address || '');
  if ((value.address || '') !== sent) {
    setSent(value.address || '');
    setParts(splitAddress(value.address));
  }

  useEffect(() => {
    let live = true;
    loadProvinces().then((rows) => live && setProvinces(rows));
    return () => {
      live = false;
    };
  }, []);

  const province = provinces.find((p) => same(p.name, value.province || ''));
  const city = province?.cities.find(([, name]) => same(name, value.city || ''));

  useEffect(() => {
    if (!province) return undefined;
    let live = true;
    loadBarangays(province.code).then((rows) => live && setBarangays(rows));
    return () => {
      live = false;
    };
  }, [province]);

  const send = (address) => {
    setSent(address);
    return address;
  };

  const setAddress = (next) => {
    setParts(next);
    onChange({ address: send(joinAddress(next.street, next.barangay)) });
  };

  const pickProvince = (name) => {
    const match = provinces.find((p) => same(p.name, name));
    const changed = !same(name, value.province || '');
    onChange({
      province: name,
      region: match?.region || '',
      ...(changed ? { city: '', address: send(joinAddress(street, '')) } : {}),
    });
    if (changed) setParts({ street, barangay: '' });
  };

  const pickCity = (name) => {
    const changed = !same(name, value.city || '');
    onChange({ city: name, ...(changed ? { address: send(joinAddress(street, '')) } : {}) });
    if (changed) setParts({ street, barangay: '' });
  };

  return (
    <div className="form-grid">
      <div className="field">
        <label className="field-label" htmlFor={`${idPrefix}_province`}>
          Province
        </label>
        <Combo
          id={`${idPrefix}_province`}
          value={value.province || ''}
          options={provinces.map((p) => p.name)}
          onChange={pickProvince}
          placeholder="Start typing, e.g. Lanao del Norte"
        />
        {province && <span className="field-hint">{province.region}</span>}
      </div>
      <div className="field">
        <label className="field-label" htmlFor={`${idPrefix}_city`}>
          City or municipality <span className="req">*</span>
        </label>
        <Combo
          id={`${idPrefix}_city`}
          value={value.city || ''}
          options={province ? province.cities.map(([, name]) => name) : []}
          onChange={pickCity}
          placeholder={province ? 'Start typing' : 'Choose the province first'}
          required
        />
      </div>
      <div className="field">
        <label className="field-label" htmlFor={`${idPrefix}_barangay`}>
          Barangay
        </label>
        <Combo
          id={`${idPrefix}_barangay`}
          value={barangay}
          options={city ? barangays[city[0]] || [] : []}
          onChange={(name) => setAddress({ street, barangay: name })}
          placeholder={city ? 'Start typing' : 'Choose the city first'}
        />
      </div>
      <div className="field">
        <label className="field-label" htmlFor={`${idPrefix}_street`}>
          Street
        </label>
        <input
          id={`${idPrefix}_street`}
          className="input"
          placeholder="House no., street, purok — optional"
          value={street}
          onChange={(event) => setAddress({ street: event.target.value, barangay })}
        />
      </div>
    </div>
  );
}

const SHOWN = 80;

/* A text box with a list of matches under it. Names that start with what was
   typed come first, then names that merely contain it. */
function Combo({ id, value, options, onChange, placeholder, required }) {
  const listId = useId();
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);

  const matches = useMemo(() => {
    const term = value.trim().toLowerCase();
    if (!term) return options.slice(0, SHOWN);
    const starts = [];
    const contains = [];
    for (const name of options) {
      const at = name.toLowerCase().indexOf(term);
      if (at === 0) starts.push(name);
      else if (at > 0) contains.push(name);
    }
    return [...starts, ...contains].slice(0, SHOWN);
  }, [value, options]);

  // Nothing to suggest once the box already holds the one listed name.
  const showing = open && matches.length > 0 && !(matches.length === 1 && same(matches[0], value));

  const pick = (name) => {
    onChange(name);
    setOpen(false);
  };

  const onKeyDown = (event) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      setOpen(true);
      const step = event.key === 'ArrowDown' ? 1 : -1;
      setActive((i) => (matches.length ? (i + step + matches.length) % matches.length : 0));
    } else if (event.key === 'Enter' && showing) {
      event.preventDefault();
      pick(matches[active] ?? matches[0]);
    } else if (event.key === 'Escape') {
      setOpen(false);
    }
  };

  return (
    <div className="combo">
      <input
        id={id}
        className="input"
        role="combobox"
        aria-expanded={showing}
        aria-controls={listId}
        aria-autocomplete="list"
        autoComplete="off"
        placeholder={placeholder}
        value={value}
        required={required}
        onChange={(event) => {
          onChange(event.target.value);
          setActive(0);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={onKeyDown}
      />
      {showing && (
        <div className="dropdown combo-list" id={listId} role="listbox">
          {matches.map((name, i) => (
            <button
              key={name}
              type="button"
              role="option"
              aria-selected={i === active}
              className={`dropdown-item${i === active ? ' is-active' : ''}`}
              // mousedown, so the pick lands before the box loses focus
              onMouseDown={(event) => {
                event.preventDefault();
                pick(name);
              }}
            >
              {name}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { ChevronDown } from 'lucide-react';

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
    <div className="stack">
      <div className="field">
        <label className="field-label" htmlFor={`${idPrefix}_province`}>
          Province <span className="req">*</span>
        </label>
        <Combo
          id={`${idPrefix}_province`}
          value={value.province || ''}
          options={provinces.map((p) => p.name)}
          onChange={pickProvince}
          placeholder="Type or choose a province"
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
          disabled={!province}
          placeholder={province ? 'Type or choose a city or municipality' : 'Choose the province first'}
          required
        />
      </div>
      <div className="form-grid">
        <div className="field">
          <label className="field-label" htmlFor={`${idPrefix}_barangay`}>
            Barangay
          </label>
          <Combo
            id={`${idPrefix}_barangay`}
            value={barangay}
            options={city ? barangays[city[0]] || [] : []}
            onChange={(name) => setAddress({ street, barangay: name })}
            disabled={!city}
            placeholder={city ? 'Type or choose a barangay' : 'Choose the city first'}
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
    </div>
  );
}

/* A text box with a list under it. Clicking it shows the whole list to scroll
   through; typing narrows it to the names that start with what was typed. */
function Combo({ id, value, options, onChange, placeholder, required, disabled }) {
  const listId = useId();
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  // True from opening the box until the first key typed: the whole list is
  // offered then, even with a name already picked, so it can be changed.
  const [browsing, setBrowsing] = useState(false);

  const matches = useMemo(() => {
    const term = value.trim().toLowerCase();
    if (!term || (browsing && options.some((name) => same(name, term)))) return options;
    return options.filter((name) => name.toLowerCase().startsWith(term));
  }, [value, options, browsing]);

  // Nothing to suggest once the box already holds the one listed name.
  const showing = open && !disabled && matches.length > 0;

  const pick = (name) => {
    onChange(name);
    setOpen(false);
  };

  // "lanao del norte" typed in full counts as picking "Lanao del Norte".
  const settle = () => {
    setOpen(false);
    const listed = options.find((name) => same(name, value));
    if (listed && listed !== value) onChange(listed);
  };

  const openList = () => {
    const at = options.findIndex((name) => same(name, value));
    setActive(at < 0 ? 0 : at);
    setBrowsing(true);
    setOpen(true);
  };

  // Keep the highlighted name in view while moving through a long list.
  const listRef = useRef(null);
  useEffect(() => {
    listRef.current?.children[active]?.scrollIntoView({ block: 'nearest' });
  }, [active, open]);

  const onKeyDown = (event) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (!open) return openList();
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
      <ChevronDown size={16} className="combo-chevron" aria-hidden="true" />
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
        disabled={disabled}
        onChange={(event) => {
          onChange(event.target.value);
          setBrowsing(false);
          setActive(0);
          setOpen(true);
        }}
        onFocus={openList}
        onClick={() => !open && openList()}
        onBlur={settle}
        onKeyDown={onKeyDown}
      />
      {showing && (
        <div className="dropdown combo-list" id={listId} role="listbox" ref={listRef}>
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

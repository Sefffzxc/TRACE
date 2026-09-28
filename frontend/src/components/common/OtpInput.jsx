import { useEffect, useRef } from 'react';

/**
 * Six single-character boxes that behave the way people expect: typing moves
 * forward, backspace moves back, and pasting a whole code fills every box.
 */
export default function OtpInput({ value, onChange, onComplete, length = 6, disabled }) {
  const refs = useRef([]);

  useEffect(() => {
    refs.current[0]?.focus();
  }, []);

  const setDigit = (index, digit) => {
    const next = value.padEnd(length, ' ').split('');
    next[index] = digit || ' ';
    const joined = next.join('').replace(/\s+$/, '');
    onChange(joined.trimEnd());

    if (digit && index < length - 1) refs.current[index + 1]?.focus();

    const filled = joined.replace(/\s/g, '');
    if (filled.length === length) onComplete?.(filled);
  };

  const onKeyDown = (index) => (event) => {
    if (event.key === 'Backspace' && !event.currentTarget.value && index > 0) {
      refs.current[index - 1]?.focus();
    }
    if (event.key === 'ArrowLeft' && index > 0) refs.current[index - 1]?.focus();
    if (event.key === 'ArrowRight' && index < length - 1) refs.current[index + 1]?.focus();
  };

  const onPaste = (event) => {
    const text = (event.clipboardData.getData('text') || '').replace(/\D/g, '').slice(0, length);
    if (!text) return;
    event.preventDefault();
    onChange(text);
    refs.current[Math.min(text.length, length - 1)]?.focus();
    if (text.length === length) onComplete?.(text);
  };

  return (
    <div className="otp-inputs" onPaste={onPaste}>
      {Array.from({ length }).map((_, index) => (
        <input
          key={index}
          ref={(element) => {
            refs.current[index] = element;
          }}
          className={value[index] ? 'filled' : ''}
          type="text"
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={1}
          disabled={disabled}
          value={value[index] || ''}
          onChange={(event) => setDigit(index, event.target.value.replace(/\D/g, '').slice(-1))}
          onKeyDown={onKeyDown(index)}
          aria-label={`Digit ${index + 1}`}
        />
      ))}
    </div>
  );
}

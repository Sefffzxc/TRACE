/**
 * The TRACE mark: a gold angled wing beside a T whose stem hooks and ends in a
 * sharp cut. The same shapes as public/brand/trace-mark.svg.
 *
 * The body of the T takes `currentColor`, so it follows whatever surface it
 * sits on (white on the navy sidebar and sign-in panel); the wing is always
 * gold.
 */
export default function TraceMark({ size = 24, title = 'TRACE', className }) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="2 6 60 56"
      role="img"
      aria-label={title}
    >
      <path d="M4 12 H26 L23 24 H14 Z" fill="#f0b429" />
      <path
        d="M29 12 H60 L55 24 H45 C38 24 38 34 42 41 L52 58 H41 C31 50 27 39 29 24 Z"
        fill="currentColor"
      />
    </svg>
  );
}

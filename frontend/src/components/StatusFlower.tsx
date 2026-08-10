// The 4-petal status flower -- Pulse's signature
// status glyph, used for event severity, project health, blockers and
// actions. Drawn inline (basic shape, per the asset rules) so the fill
// can be driven by the semantic palette in constants.ts.

export default function StatusFlower({
  color,
  size = 26,
  title,
}: {
  color: string;
  size?: number;
  title?: string;
}) {
  const petal = (
    <ellipse cx="0" cy="-6.4" rx="3.4" ry="6.2" fill={color} />
  );
  return (
    <svg
      width={size}
      height={size}
      viewBox="-11 -11 22 22"
      role="img"
      aria-label={title}
      className="shrink-0"
    >
      {title ? <title>{title}</title> : null}
      <g transform="rotate(0)">{petal}</g>
      <g transform="rotate(60)">{petal}</g>
      <g transform="rotate(120)">{petal}</g>
      <g transform="rotate(180)">{petal}</g>
      <g transform="rotate(240)">{petal}</g>
      <g transform="rotate(300)">{petal}</g>
      <circle cx="0" cy="0" r="2.4" fill={color} />
    </svg>
  );
}

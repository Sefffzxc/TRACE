import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { axisProps, chartColors } from './chartTheme';

/** A tooltip that matches the app's cards rather than Recharts' default. */
function ChartTooltip({ active, payload, label, suffix = '' }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="chart-tooltip">
      <div className="tt-title">{label}</div>
      {payload.map((entry) => (
        <div className="tt-row" key={entry.dataKey || entry.name}>
          <i style={{ background: entry.color || entry.payload?.fill }} />
          <span>{entry.name}</span>
          <b>
            {typeof entry.value === 'number' ? entry.value.toFixed(entry.value % 1 ? 1 : 0) : entry.value}
            {suffix}
          </b>
        </div>
      ))}
    </div>
  );
}

/* ── Average PACE completion per section ─────────────────────────────────── */
export function SectionPaceChart({ data = [] }) {
  const colors = chartColors();
  if (!data.length) return <NoData />;

  return (
    <ResponsiveContainer width="100%" height={260}>
      <BarChart data={data} margin={{ top: 8, right: 12, left: -18, bottom: 4 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={colors.grid} vertical={false} />
        <XAxis dataKey="section" {...axisProps(colors)} interval={0} angle={-12} textAnchor="end" height={54} />
        <YAxis domain={[0, 100]} unit="%" {...axisProps(colors)} />
        <Tooltip content={<ChartTooltip suffix="%" />} cursor={{ fill: colors.grid, opacity: 0.35 }} />
        <Bar dataKey="average" name="Average completion" radius={[5, 5, 0, 0]} maxBarSize={44}>
          {data.map((row) => (
            <Cell
              key={row.section}
              fill={row.average >= 70 ? colors.low : row.average >= 45 ? colors.gold : colors.high}
            />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

/* ── Actual completion against the expected curve ────────────────────────── */
export function TrendChart({ data = [] }) {
  const colors = chartColors();
  if (!data.length) return <NoData />;

  return (
    <ResponsiveContainer width="100%" height={262}>
      <LineChart data={data} margin={{ top: 10, right: 14, left: -18, bottom: 4 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={colors.grid} vertical={false} />
        <XAxis dataKey="label" {...axisProps(colors)} />
        <YAxis domain={[0, 100]} unit="%" {...axisProps(colors)} />
        <Tooltip content={<ChartTooltip suffix="%" />} />
        <Legend
          verticalAlign="top"
          align="right"
          height={28}
          wrapperStyle={{ fontSize: 11, color: colors.axis }}
        />
        <Line
          type="monotone"
          dataKey="expected"
          name="Expected"
          stroke={colors.axis}
          strokeDasharray="5 4"
          strokeWidth={1.6}
          dot={false}
        />
        <Line
          type="monotone"
          dataKey="actual"
          name="Actual"
          stroke={colors.brand}
          strokeWidth={2.4}
          dot={{ r: 3, fill: colors.brand, strokeWidth: 0 }}
          activeDot={{ r: 5 }}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}

/* ── Risk distribution donut ─────────────────────────────────────────────── */
export function RiskDonut({ data = [], total = 0, emptyMessage = 'No PACE records yet.' }) {
  const colors = chartColors();
  const tone = {
    low: colors.low,
    moderate: colors.moderate,
    high: colors.high,
    critical: colors.critical,
  };
  const populated = data.filter((row) => row.value > 0);
  if (!populated.length) return <NoData message={emptyMessage} />;

  return (
    <div className="donut-wrap">
      <ResponsiveContainer width="100%" height={210}>
        <PieChart>
          <Pie
            data={populated}
            dataKey="value"
            nameKey="name"
            innerRadius={62}
            outerRadius={90}
            paddingAngle={2}
            strokeWidth={0}
          >
            {populated.map((row) => (
              <Cell key={row.key} fill={tone[row.key] || colors.brand} />
            ))}
          </Pie>
          <Tooltip content={<ChartTooltip />} />
        </PieChart>
      </ResponsiveContainer>
      <div className="donut-center">
        <span className="v">{total}</span>
        <span className="l">Students</span>
      </div>
    </div>
  );
}

/* ── Per-subject completion ──────────────────────────────────────────────── */
export function SubjectChart({ data = [] }) {
  const colors = chartColors();
  if (!data.length) return <NoData />;

  return (
    <ResponsiveContainer width="100%" height={Math.max(200, data.length * 38)}>
      <BarChart data={data} layout="vertical" margin={{ top: 4, right: 20, left: 8, bottom: 4 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={colors.grid} horizontal={false} />
        <XAxis type="number" domain={[0, 100]} unit="%" {...axisProps(colors)} />
        <YAxis type="category" dataKey="subject" width={110} {...axisProps(colors)} />
        <Tooltip content={<ChartTooltip suffix="%" />} cursor={{ fill: colors.grid, opacity: 0.35 }} />
        <Bar dataKey="average" name="Average completion" radius={[0, 5, 5, 0]} maxBarSize={20}>
          {data.map((row) => (
            <Cell
              key={row.subject}
              fill={row.average >= 70 ? colors.low : row.average >= 45 ? colors.gold : colors.high}
            />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

/* ── Grade-based views, for a school that keeps a class record ──────────────
   The axis starts at 50, not 0: grades sit in a narrow band above the passing
   mark, and from zero every bar looks the same height. Anything lower still
   stretches the axis down rather than being clipped. */
const GRADE_DOMAIN = [50, 100];

function gradeFill(colors, average, passingMark) {
  if (average >= passingMark + 5) return colors.low;
  if (average >= passingMark) return colors.gold;
  return colors.high;
}

function GradeTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  const row = payload[0].payload;
  return (
    <div className="chart-tooltip">
      <div className="tt-title">{label}</div>
      <div className="tt-row">
        <i style={{ background: payload[0].color || payload[0].payload?.fill }} />
        <span>Average grade</span>
        <b>{row.average.toFixed(1)}</b>
      </div>
      <div className="tt-row">
        <span>Below passing</span>
        <b>
          {row.below} of {row.students}
        </b>
      </div>
    </div>
  );
}

function PassingLine({ colors, passingMark, vertical = false }) {
  const position = vertical ? { x: passingMark } : { y: passingMark };
  return (
    <ReferenceLine
      {...position}
      stroke={colors.critical}
      strokeDasharray="4 4"
      label={{
        value: `Passing ${passingMark}`,
        position: vertical ? 'top' : 'insideTopRight',
        fill: colors.axis,
        fontSize: 10,
      }}
    />
  );
}

/* ── Average grade per section ───────────────────────────────────────────── */
export function SectionGradeChart({ data = [], passingMark = 75 }) {
  const colors = chartColors();
  if (!data.length) return <NoData message="No grades recorded yet." />;

  return (
    <ResponsiveContainer width="100%" height={260}>
      <BarChart data={data} margin={{ top: 8, right: 12, left: -18, bottom: 4 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={colors.grid} vertical={false} />
        <XAxis dataKey="section" {...axisProps(colors)} interval={0} angle={-12} textAnchor="end" height={54} />
        <YAxis domain={GRADE_DOMAIN} {...axisProps(colors)} />
        <Tooltip content={<GradeTooltip />} cursor={{ fill: colors.grid, opacity: 0.35 }} />
        <PassingLine colors={colors} passingMark={passingMark} />
        <Bar dataKey="average" name="Average grade" radius={[5, 5, 0, 0]} maxBarSize={44}>
          {data.map((row) => (
            <Cell key={row.section} fill={gradeFill(colors, row.average, passingMark)} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

/* ── Average grade per subject ───────────────────────────────────────────── */
export function SubjectGradeChart({ data = [], passingMark = 75 }) {
  const colors = chartColors();
  if (!data.length) return <NoData message="No grades recorded yet." />;

  return (
    <ResponsiveContainer width="100%" height={Math.max(200, data.length * 38)}>
      <BarChart data={data} layout="vertical" margin={{ top: 16, right: 20, left: 8, bottom: 4 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={colors.grid} horizontal={false} />
        <XAxis type="number" domain={GRADE_DOMAIN} {...axisProps(colors)} />
        <YAxis type="category" dataKey="subject" width={110} {...axisProps(colors)} />
        <Tooltip content={<GradeTooltip />} cursor={{ fill: colors.grid, opacity: 0.35 }} />
        <PassingLine colors={colors} passingMark={passingMark} vertical />
        <Bar dataKey="average" name="Average grade" radius={[0, 5, 5, 0]} maxBarSize={20}>
          {data.map((row) => (
            <Cell key={row.subject} fill={gradeFill(colors, row.average, passingMark)} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

/* ── Average grade per grading period, against the passing mark ──────────── */
export function PeriodGradeChart({ data = [] }) {
  const colors = chartColors();
  if (!data.length) return <NoData message="No grades recorded yet." />;

  return (
    <ResponsiveContainer width="100%" height={262}>
      <LineChart data={data} margin={{ top: 10, right: 14, left: -18, bottom: 4 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={colors.grid} vertical={false} />
        <XAxis dataKey="label" {...axisProps(colors)} />
        <YAxis domain={GRADE_DOMAIN} {...axisProps(colors)} />
        <Tooltip content={<ChartTooltip />} />
        <Legend verticalAlign="top" align="right" height={28} wrapperStyle={{ fontSize: 11, color: colors.axis }} />
        <Line
          type="monotone"
          dataKey="passing"
          name="Passing mark"
          stroke={colors.axis}
          strokeDasharray="5 4"
          strokeWidth={1.6}
          dot={false}
        />
        <Line
          type="monotone"
          dataKey="average"
          name="Average grade"
          stroke={colors.brand}
          strokeWidth={2.4}
          dot={{ r: 3, fill: colors.brand, strokeWidth: 0 }}
          activeDot={{ r: 5 }}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}

function NoData({ message = 'Nothing to chart yet.' }) {
  return (
    <div
      style={{
        height: 210,
        display: 'grid',
        placeItems: 'center',
        color: 'var(--text-faint)',
        fontSize: 'var(--text-sm)',
      }}
    >
      {message}
    </div>
  );
}

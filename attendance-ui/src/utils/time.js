// Time helpers. All attendance times are displayed in Cambodia time (UTC+7, no
// DST), regardless of the kiosk/browser's own timezone. Override with VITE_APP_TZ.
export const APP_TZ = import.meta.env.VITE_APP_TZ || "Asia/Phnom_Penh";

// Cambodia calendar day (YYYY-MM-DD) — matches the server's attendance bucket.
export const todayStr = () =>
  new Date().toLocaleDateString("en-CA", { timeZone: APP_TZ });

// Parse a server timestamp into a Date. The DB stores UTC, but if the serialized
// ISO string carries no timezone (e.g. "2026-07-28T08:44:54"), the browser would
// wrongly read it as local — so we treat a tz-less ISO string as UTC.
const toDate = (v) => {
  if (v instanceof Date) return v;
  if (typeof v === "string") {
    const isIso = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(v);
    const hasTz = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(v);
    if (isIso && !hasTz) return new Date(v + "Z");
  }
  return new Date(v);
};

// Local time-of-day, e.g. "3:54:21 PM", in Cambodia time. Safe on null/invalid.
export const fmtTime = (v) => {
  if (!v) return "";
  const d = toDate(v);
  return isNaN(d)
    ? ""
    : d.toLocaleTimeString("en-US", {
        hour: "numeric",
        minute: "2-digit",
        timeZone: APP_TZ,
      });
};

// Local date, in Cambodia time. Safe on null/invalid.
export const fmtDate = (v) => {
  if (!v) return "";
  const d = toDate(v);
  return isNaN(d) ? "" : d.toLocaleDateString("en-US", { timeZone: APP_TZ });
};

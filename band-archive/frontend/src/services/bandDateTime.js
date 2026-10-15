const dateTimePattern = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?(Z|[+-]\d{2}:\d{2})?)?$/i;

const parseTimestamp = (value) => {
  if (typeof value !== 'string' || !value.trim()) return null;
  const match = dateTimePattern.exec(value.trim());
  if (!match) return null;
  const [, yearText, monthText, dayText, hourText, minuteText, secondText, , zone] = match;
  const year = Number(yearText); const month = Number(monthText); const day = Number(dayText);
  const calendar = new Date(0);
  calendar.setUTCFullYear(year, month - 1, day);
  calendar.setUTCHours(0, 0, 0, 0);
  if (calendar.getUTCFullYear() !== year || calendar.getUTCMonth() !== month - 1 || calendar.getUTCDate() !== day) return null;
  if (hourText !== undefined) {
    if (Number(hourText) > 23 || Number(minuteText) > 59 || Number(secondText || 0) > 59) return null;
    if (zone && /^[+-]/.test(zone)) {
      const offset = /^([+-])(\d{2}):(\d{2})$/.exec(zone);
      if (!offset || Number(offset[2]) > 23 || Number(offset[3]) > 59) return null;
    }
  }
  const normalized = hourText === undefined
    ? `${yearText}-${monthText}-${dayText}T00:00:00Z`
    : `${yearText}-${monthText}-${dayText}T${hourText}:${minuteText}:${secondText || '00'}${match[7] ? `.${match[7]}` : ''}${zone || 'Z'}`;
  const parsed = new Date(normalized);
  return Number.isFinite(parsed.getTime()) ? parsed : null;
};

const kstFormatter = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
});

export const formatInviteCreatedAt = (value) => {
  const date = parseTimestamp(value);
  if (!date) return '시간 정보 없음';
  const parts = Object.fromEntries(kstFormatter.formatToParts(date).map(({ type, value: part }) => [type, part]));
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute} KST`;
};

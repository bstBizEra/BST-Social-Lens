import type { SocialRecord } from './types';

export const CSV_COLUMNS: (keyof SocialRecord)[] = [
  'key',
  'platform',
  'post_id',
  'permalink',
  'container_id',
  'container_name',
  'author_name',
  'author_id',
  'author_hash',
  'author_url',
  'text',
  'lang',
  'created_at',
  'captured_at',
  'reactions_total',
  'comments_count',
  'shares_count',
  'views_count',
  'media',
  'hashtags',
  'parser_version',
  'synced',
];

const csvCell = (v: unknown): string => {
  if (v === undefined || v === null) return '';
  const s = typeof v === 'object' ? JSON.stringify(v) : String(v);
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};

export function toCsv(records: SocialRecord[]): string {
  const head = CSV_COLUMNS.join(',');
  const rows = records.map((r) => CSV_COLUMNS.map((c) => csvCell(r[c])).join(','));
  return '﻿' + [head, ...rows].join('\r\n');
}

export function toNdjson(records: SocialRecord[]): string {
  return records.map((r) => JSON.stringify(r)).join('\n') + '\n';
}

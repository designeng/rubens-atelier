// Urgency is a friendly name for the item `priority` (-100..100): a higher value is scheduled first.
export const URGENCY_LEVELS = [
  { label: "Low", value: -10 },
  { label: "Normal", value: 0 },
  { label: "High", value: 10 },
  { label: "Urgent", value: 50 },
];

export const DEFAULT_URGENCY = 0;

export function urgencyLabel(priority: number): string {
  return URGENCY_LEVELS.find((l) => l.value === priority)?.label ?? `priority ${priority}`;
}

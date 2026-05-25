import { badgeClassForStatus } from "../lib/format";

export function Badge({ status, children, className = "" }) {
  const text = children ?? status ?? "-";
  return <span className={`${badgeClassForStatus(status)} ${className}`.trim()}>{text}</span>;
}

import type { ReactNode, SVGProps } from "react";

export type BrandIconName =
  | "overview"
  | "services"
  | "calendar"
  | "history"
  | "users"
  | "activity"
  | "settings"
  | "telegram"
  | "check"
  | "clock"
  | "warning";
const shapes: Record<BrandIconName, ReactNode> = {
  overview: (
    <>
      <rect x="3" y="3" width="8" height="8" rx="2" />
      <rect x="13" y="3" width="8" height="8" rx="2" />
      <rect x="3" y="13" width="8" height="8" rx="2" />
      <rect x="13" y="13" width="8" height="8" rx="2" />
    </>
  ),
  services: (
    <>
      <rect x="3" y="3" width="18" height="5" rx="2" />
      <rect x="3" y="10" width="18" height="5" rx="2" />
      <rect x="3" y="17" width="18" height="4" rx="2" />
    </>
  ),
  calendar: (
    <path
      fillRule="evenodd"
      d="M7 2a1 1 0 0 1 1 1v1h8V3a1 1 0 1 1 2 0v1h1a3 3 0 0 1 3 3v12a3 3 0 0 1-3 3H5a3 3 0 0 1-3-3V7a3 3 0 0 1 3-3h1V3a1 1 0 0 1 1-1ZM5 9v2h14V9H5Zm1 5v3h3v-3H6Zm5 0v3h3v-3h-3Zm5 0v3h3v-3h-3Z"
    />
  ),
  history: (
    <path
      fillRule="evenodd"
      d="M12 2a10 10 0 1 1 0 20 10 10 0 0 1 0-20Zm-1 4v7h6v-2h-4V6h-2Z"
    />
  ),
  users: (
    <>
      <circle cx="9" cy="7" r="4" />
      <path d="M1 19a8 8 0 0 1 16 0v1H1v-1Z" />
      <path d="M16 3.4a4 4 0 0 1 0 7.2 6 6 0 0 0 0-7.2ZM18 13c3.1.5 5 2.7 5 6v1h-4v-1a10 10 0 0 0-2-6h1Z" />
    </>
  ),
  activity: (
    <>
      <rect x="3" y="4" width="4" height="4" rx="1" />
      <rect x="9" y="4" width="12" height="4" rx="1" />
      <rect x="3" y="10" width="4" height="4" rx="1" />
      <rect x="9" y="10" width="12" height="4" rx="1" />
      <rect x="3" y="16" width="4" height="4" rx="1" />
      <rect x="9" y="16" width="12" height="4" rx="1" />
    </>
  ),
  settings: (
    <path
      fillRule="evenodd"
      d="m9.3 2-.6 2.4-2 .9-2.2-.7-2.7 4.7 1.7 1.7v2l-1.7 1.7 2.7 4.7 2.3-.7 1.9 1 .6 2.3h5.4l.6-2.4 2-.9 2.2.7 2.7-4.7-1.7-1.7v-2l1.7-1.7-2.7-4.7-2.3.7-1.9-1-.6-2.3H9.3ZM12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Z"
    />
  ),
  telegram: (
    <path d="m21.4 2.7-3.2 18c-.2 1-1 1.2-1.8.7l-5-3.7-2.4 2.4c-.3.3-.5.5-1 .5l.4-5.1 9.4-8.5c.4-.4-.1-.6-.6-.3L5.6 14 1 12.6c-1-.3-1-1 .2-1.5L20 2.2c.8-.3 1.5.2 1.4.5Z" />
  ),
  check: (
    <path d="M20.7 5.3a2 2 0 0 1 0 2.8l-10 10a2 2 0 0 1-2.8 0l-4.6-4.6a2 2 0 0 1 2.8-2.8l3.2 3.2 8.6-8.6a2 2 0 0 1 2.8 0Z" />
  ),
  clock: (
    <path
      fillRule="evenodd"
      d="M12 2a10 10 0 1 1 0 20 10 10 0 0 1 0-20Zm-1 4v7l5 3 1-1.7-4-2.4V6h-2Z"
    />
  ),
  warning: (
    <path
      fillRule="evenodd"
      d="M10.3 3a2 2 0 0 1 3.4 0l8 14a2 2 0 0 1-1.7 3H4a2 2 0 0 1-1.7-3l8-14ZM11 8v6h2V8h-2Zm0 8v2h2v-2h-2Z"
    />
  ),
};
export default function BrandIcon({
  name,
  size = 20,
  ...props
}: { name: BrandIconName; size?: number } & SVGProps<SVGSVGElement>) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="currentColor"
      aria-hidden="true"
      focusable="false"
      {...props}
    >
      {shapes[name]}
    </svg>
  );
}

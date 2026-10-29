// A few line icons in one weight (1.75 px at 24), drawn to sit beside 15 to 17 px text.
import type { SVGProps } from "react";

const base = (size: number): SVGProps<SVGSVGElement> => ({
  width: size,
  height: size,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.75,
  strokeLinecap: "round",
  strokeLinejoin: "round",
  "aria-hidden": true,
});

export const ArrowUpRight = ({ size = 16 }: { size?: number }) => (
  <svg {...base(size)}>
    <path d="M7 17 17 7M8 7h9v9" />
  </svg>
);
export const Pause = ({ size = 16 }: { size?: number }) => (
  <svg {...base(size)}>
    <path d="M9 5v14M15 5v14" />
  </svg>
);
export const Play = ({ size = 16 }: { size?: number }) => (
  <svg {...base(size)}>
    <path d="M8 5.5v13l11-6.5z" fill="currentColor" stroke="none" />
  </svg>
);
export const Replay = ({ size = 16 }: { size?: number }) => (
  <svg {...base(size)}>
    <path d="M4 12a8 8 0 1 0 2.4-5.7M4 4v4h4" />
  </svg>
);
export const Check = ({ size = 14 }: { size?: number }) => (
  <svg {...base(size)}>
    <path d="m5 12.5 4.5 4.5L19 7.5" />
  </svg>
);
export const Cross = ({ size = 14 }: { size?: number }) => (
  <svg {...base(size)}>
    <path d="M6 6l12 12M18 6 6 18" />
  </svg>
);
export const Shield = ({ size = 15 }: { size?: number }) => (
  <svg {...base(size)}>
    <path d="M12 3 5 6v6c0 4.2 3 7.5 7 9 4-1.5 7-4.8 7-9V6z" />
  </svg>
);
export const Layers = ({ size = 22 }: { size?: number }) => (
  <svg {...base(size)}>
    <path d="m12 3 9 5-9 5-9-5zM3 13l9 5 9-5M3 17.5l9 5 9-5" />
  </svg>
);
export const Scan = ({ size = 22 }: { size?: number }) => (
  <svg {...base(size)}>
    <path d="M4 8V4h4M20 8V4h-4M4 16v4h4M20 16v4h-4" />
    <circle cx="12" cy="12" r="2.6" fill="currentColor" stroke="none" />
  </svg>
);
export const CheckCircle = ({ size = 22 }: { size?: number }) => (
  <svg {...base(size)}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="m8.5 12.3 2.4 2.4 4.8-5" />
  </svg>
);
export const Chat = ({ size = 22 }: { size?: number }) => (
  <svg {...base(size)}>
    <path d="M5 5h14a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H10l-4.5 3.5V16H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z" />
  </svg>
);
export const Film = ({ size = 22 }: { size?: number }) => (
  <svg {...base(size)}>
    <rect x="6.5" y="3" width="11" height="18" rx="2" />
    <path d="M10.5 9.5v5l4-2.5z" fill="currentColor" stroke="none" />
  </svg>
);
export const Package = ({ size = 22 }: { size?: number }) => (
  <svg {...base(size)}>
    <path d="m12 3 8 4.5v9L12 21l-8-4.5v-9z" />
    <path d="m4 7.5 8 4.5 8-4.5M12 12v9" />
  </svg>
);
export const Undo = ({ size = 13 }: { size?: number }) => (
  <svg {...base(size)}>
    <path d="M9 7 4 12l5 5M4 12h11a5 5 0 0 1 0 10h-1" />
  </svg>
);

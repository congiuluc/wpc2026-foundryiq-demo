import type { SVGProps } from "react";

function Svg({ children, className = "ui-icon", ...props }: SVGProps<SVGSVGElement>) {
  return (
    <svg
      viewBox="0 0 20 20"
      width="18"
      height="18"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      className={className}
      {...props}
    >
      {children}
    </svg>
  );
}

export function SparkIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Svg {...props}>
      <path d="M10 2.25 11.9 7.1 16.75 9 11.9 10.9 10 15.75 8.1 10.9 3.25 9 8.1 7.1Z" />
      <path d="M15.5 14v3M14 15.5h3" />
    </Svg>
  );
}

export function GlobeIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Svg {...props}>
      <circle cx="10" cy="10" r="7.25" />
      <path d="M3 10h14M10 2.75c2.1 2 3.25 4.55 3.25 7.25S12.1 15.25 10 17.25c-2.1-2-3.25-4.55-3.25-7.25S7.9 4.75 10 2.75Z" />
    </Svg>
  );
}

export function ShieldIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Svg {...props}>
      <path d="M10 2.5 16 5v4.6c0 4-2.55 6.85-6 8.15-3.45-1.3-6-4.15-6-8.15V5Z" />
      <path d="m7.75 10.1 1.6 1.6 3.15-3.2" />
    </Svg>
  );
}

export function CompareIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Svg {...props}>
      <rect x="2.75" y="3.5" width="6" height="13" rx="1.75" />
      <rect x="11.25" y="3.5" width="6" height="13" rx="1.75" />
    </Svg>
  );
}

export function BotIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Svg {...props}>
      <rect x="3.5" y="6" width="13" height="10" rx="2.75" />
      <path d="M10 3v3M7.5 10.5h.01M12.5 10.5h.01M7.75 13.5h4.5" />
    </Svg>
  );
}

export function LayersIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Svg {...props}>
      <path d="M10 3 17 6.75 10 10.5 3 6.75Z" />
      <path d="m3 10.25 7 3.75 7-3.75M3 13.75 10 17.5l7-3.75" />
    </Svg>
  );
}

export function FlaskIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Svg {...props}>
      <path d="M8 3h4M8.75 3v4.4L4.6 14.3A1.75 1.75 0 0 0 6.1 17h7.8a1.75 1.75 0 0 0 1.5-2.7l-4.15-6.9V3" />
      <path d="M6.3 12.5h7.4" />
    </Svg>
  );
}

export function StageIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Svg {...props}>
      <path d="M3.5 7V4.5H6M14 4.5h2.5V7M16.5 13v2.5H14M6 15.5H3.5V13" />
      <rect x="6.5" y="6.75" width="7" height="6.5" rx="1.25" />
    </Svg>
  );
}

export function MoonIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Svg {...props}>
      <path d="M15.8 12.4A6.5 6.5 0 0 1 7.6 4.2a6.5 6.5 0 1 0 8.2 8.2Z" />
    </Svg>
  );
}

export function SunIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Svg {...props}>
      <circle cx="10" cy="10" r="3.5" />
      <path d="M10 2.5v2M10 15.5v2M2.5 10h2M15.5 10h2M4.7 4.7l1.4 1.4M13.9 13.9l1.4 1.4M15.3 4.7l-1.4 1.4M6.1 13.9l-1.4 1.4" />
    </Svg>
  );
}

export function SendIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Svg strokeWidth="1.6" {...props}>
      <path d="M3.2 3.4 17.4 9.6a.45.45 0 0 1 0 .8L3.2 16.6a.45.45 0 0 1-.62-.53L4.6 10 2.58 3.93a.45.45 0 0 1 .62-.53Z" fill="currentColor" fillOpacity=".18" />
      <path d="M4.6 10h6.4" />
    </Svg>
  );
}

export function PlusIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Svg {...props}>
      <path d="M10 4v12M4 10h12" />
    </Svg>
  );
}

export function FlowIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Svg {...props}>
      <rect x="2.75" y="3.5" width="5" height="4.5" rx="1.25" />
      <rect x="12.25" y="3.5" width="5" height="4.5" rx="1.25" />
      <rect x="7.5" y="12" width="5" height="4.5" rx="1.25" />
      <path d="M7.75 5.75h4.5M10 5.75V12" />
    </Svg>
  );
}

export function ActivityIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Svg {...props}>
      <path d="M2.5 10h3.2l2.1-5 4.2 10 2.2-5h3.3" />
    </Svg>
  );
}

export function CopyIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Svg {...props}>
      <rect x="7" y="7" width="9.5" height="9.5" rx="1.75" />
      <path d="M4.75 13H4a1.5 1.5 0 0 1-1.5-1.5V4A1.5 1.5 0 0 1 4 2.5h7.5A1.5 1.5 0 0 1 13 4v.75" />
    </Svg>
  );
}

export function CheckIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Svg {...props}>
      <path d="m4.25 10.5 3.75 3.75 7.75-8" />
    </Svg>
  );
}

export function CloseIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Svg {...props}>
      <path d="M5 5l10 10M15 5 5 15" />
    </Svg>
  );
}

export function FolderIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Svg {...props}>
      <path d="M2.75 6A1.75 1.75 0 0 1 4.5 4.25h3.4l1.6 1.75h6A1.75 1.75 0 0 1 17.25 7.75v6.75a1.75 1.75 0 0 1-1.75 1.75h-11A1.75 1.75 0 0 1 2.75 14.5Z" />
    </Svg>
  );
}

export function LockIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Svg {...props}>
      <rect x="4.25" y="9" width="11.5" height="8" rx="2" />
      <path d="M6.75 9V6.5a3.25 3.25 0 0 1 6.5 0V9" />
    </Svg>
  );
}

export function ToolIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Svg {...props}>
      <path d="M11.8 4.2a3.8 3.8 0 0 0-4.9 4.9l-3.9 3.9a1.5 1.5 0 0 0 2.1 2.1l3.9-3.9a3.8 3.8 0 0 0 4.9-4.9l-2.2 2.2-1.9-.4-.4-1.9Z" />
    </Svg>
  );
}

export function ClockIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Svg {...props}>
      <circle cx="10" cy="10" r="7.25" />
      <path d="M10 6v4.25l2.75 1.75" />
    </Svg>
  );
}

export function TokenIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Svg {...props}>
      <ellipse cx="10" cy="5.5" rx="6" ry="2.5" />
      <path d="M4 5.5V10c0 1.38 2.69 2.5 6 2.5s6-1.12 6-2.5V5.5M4 10v4.5c0 1.38 2.69 2.5 6 2.5s6-1.12 6-2.5V10" />
    </Svg>
  );
}

export function ArrowRightIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Svg {...props}>
      <path d="M4.5 10h11M11 5.5 15.5 10 11 14.5" />
    </Svg>
  );
}

export function UserIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Svg {...props}>
      <circle cx="10" cy="6.75" r="3.25" />
      <path d="M4 16.75c.95-3.1 3.35-4.75 6-4.75s5.05 1.65 6 4.75" />
    </Svg>
  );
}

export function ChevronDownIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Svg {...props}>
      <path d="m5.5 7.75 4.5 4.5 4.5-4.5" />
    </Svg>
  );
}

export function SlidersIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Svg {...props}>
      <path d="M3 6h14M3 14h14" />
      <circle cx="7.5" cy="6" r="2" fill="currentColor" />
      <circle cx="12.5" cy="14" r="2" fill="currentColor" />
    </Svg>
  );
}

export function PanelRightIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Svg {...props}>
      <rect x="2.75" y="3.5" width="14.5" height="13" rx="2.25" />
      <path d="M12.25 3.5v13" />
    </Svg>
  );
}

export function ChevronLeftIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Svg {...props}>
      <path d="m12.25 5.5-4.5 4.5 4.5 4.5" />
    </Svg>
  );
}

export function ChevronRightIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Svg {...props}>
      <path d="m7.75 5.5 4.5 4.5-4.5 4.5" />
    </Svg>
  );
}

import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium font-mono uppercase tracking-wider",
  {
    variants: {
      variant: {
        default: "bg-elevated text-fgMuted",
        accent:  "bg-accent/20 text-accent",
        success: "bg-success/20 text-success",
        warn:    "bg-warn/20 text-warn",
        danger:  "bg-danger/20 text-danger",
        outline: "border border-border text-fgMuted",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant, className }))} {...props} />;
}

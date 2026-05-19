import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

// Button hierarchy, top → bottom:
//   primary  — the one headline action on screen (Start, Reprocess).
//              Heavier weight; pair with size="lg" for the broadcast
//              Start/Stop, regular size for everything else.
//   default  — common affirmative action (Save, Add).
//   secondary— neutral surface chip — settings, dialog "Cancel".
//   outline  — utility, often paired with an icon.
//   ghost    — icon-only or low-importance inline actions.
//   success  — start-broadcast (green, unmistakable).
//   danger   — stop-broadcast OR destructive delete (red, unmistakable).
//
// All variants share:
//   • short single-frame hover transition — no bounce, no scale
//   • inset highlight via box-shadow so flat colour fills feel dimensional
//   • focus ring uses --accent so org branding tracks
//   • active state nudges 1px down — very subtle, broadcast tools should
//     feel solid not bouncy
const buttonVariants = cva(
  [
    "inline-flex items-center justify-center gap-2 whitespace-nowrap",
    "rounded-md text-sm font-medium tracking-tight",
    "select-none",
    "transition-[background-color,box-shadow,color,transform,filter] duration-75",
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60",
    "disabled:pointer-events-none disabled:opacity-40",
    "active:translate-y-px",
  ].join(" "),
  {
    variants: {
      variant: {
        primary: [
          "bg-accent text-accentFg font-semibold",
          "shadow-[inset_0_1px_0_0_rgba(255,255,255,0.18),0_1px_0_0_rgba(0,0,0,0.4)]",
          "hover:brightness-110",
        ].join(" "),
        default: [
          "bg-accent/95 text-accentFg",
          "shadow-[inset_0_1px_0_0_rgba(255,255,255,0.14)]",
          "hover:bg-accent",
        ].join(" "),
        secondary: [
          "bg-elevated text-fg border border-border",
          "shadow-[inset_0_1px_0_0_rgba(255,255,255,0.03)]",
          "hover:border-muted hover:bg-elevated/70",
        ].join(" "),
        outline: [
          "border border-border bg-transparent text-fg",
          "hover:border-muted hover:bg-elevated/40",
        ].join(" "),
        ghost: [
          "text-fgMuted hover:text-fg hover:bg-elevated/60",
        ].join(" "),
        success: [
          "bg-success text-white font-semibold",
          "shadow-[inset_0_1px_0_0_rgba(255,255,255,0.2),0_0_0_1px_rgba(34,197,94,0.35)]",
          "hover:brightness-110",
        ].join(" "),
        danger: [
          "bg-danger text-white font-semibold",
          "shadow-[inset_0_1px_0_0_rgba(255,255,255,0.2),0_0_0_1px_rgba(220,38,38,0.4)]",
          "hover:brightness-110",
        ].join(" "),
      },
      size: {
        default: "h-9 px-4",
        sm:      "h-8 px-3 text-xs",
        lg:      "h-11 px-6 text-[15px]",
        icon:    "h-9 w-9",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return <Comp className={cn(buttonVariants({ variant, size, className }))} ref={ref} {...props} />;
  },
);
Button.displayName = "Button";

export { buttonVariants };

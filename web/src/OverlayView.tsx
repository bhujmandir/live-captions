import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { connectWs } from "./api";
import { CaptionRenderer } from "@/components/CaptionRenderer";
import { useStore } from "@/store";

// Overlay surface, served at /?overlay=1&area=...&fs=...&...
//
// Two layout modes, auto-detected from the viewport's aspect ratio:
//
//   AREA mode — PP Web Object sized to the caption area's pixel
//   rectangle. Most common pattern (e.g. area=0,0,1680,220 with a
//   1680×220 Web Object). We scale so the caption area fills the
//   viewport at 1:1 and translate so its top-left aligns with the
//   viewport's top-left. Font sizes from the URL render unmodified.
//
//   STAGE mode — PP Web Object sized to the full 1920×1080 stage.
//   We scale the whole stage to fit the viewport; the caption area
//   sits inside at its configured (X, Y). Useful when one Web Object
//   covers the entire LED wall and the caption appears in a corner.
//
// `?scale=1` URL param forces 1:1 with no translation.
//
// CaptionRenderer scales its own internal stage via `stageScale`. We
// give the renderer a fixed-size container; the renderer's scaled
// stage fills it. The container is positioned absolutely inside the
// viewport with the area-anchored translation applied via plain `left`
// / `top` (avoids a compounded CSS transform).

const STAGE_W = 1920;
const STAGE_H = 1080;

export function OverlayView() {
  useEffect(() => { connectWs(); }, []);

  const settings       = useStore((s) => s.settings);
  const qp             = new URLSearchParams(location.search);
  const wantsUnitScale = qp.get("scale") === "1";
  // `fit=width` short-circuits the aspect-based AREA/STAGE auto-detect
  // and forces the caption area to fill the full viewport width. The
  // settings store also persists this as `overlayFitWidth`; URL param
  // takes precedence (mirrors how the other overlay knobs work).
  const wantsWidthFit  = qp.get("fit") === "width" || settings.overlayFitWidth;

  const [layout, setLayout] = useState<{ scale: number; tx: number; ty: number }>({
    scale: 1, tx: 0, ty: 0,
  });

  useLayoutEffect(() => {
    if (wantsUnitScale) {
      setLayout({ scale: 1, tx: 0, ty: 0 });
      return;
    }
    const recalc = () => {
      const vw = window.innerWidth, vh = window.innerHeight;
      const aw = Math.max(1, settings.areaW), ah = Math.max(1, settings.areaH);

      if (wantsWidthFit) {
        // Width-fit override — caption area fills viewport width, no
        // letterboxing. Height comes out as ah * scale; if that exceeds
        // vh the area's bottom is clipped (operator's choice when they
        // turn this on for a non-area-shaped viewport).
        const scale = vw / aw;
        setLayout({
          scale,
          tx: -settings.areaX * scale,
          ty: -settings.areaY * scale,
        });
        return;
      }

      const areaAspect  = aw / ah;
      const stageAspect = STAGE_W / STAGE_H;
      const viewAspect  = vw / vh;
      const distToArea  = Math.abs(viewAspect - areaAspect);
      const distToStage = Math.abs(viewAspect - stageAspect);
      if (distToArea < distToStage) {
        // AREA mode — caption area fills the viewport.
        const scale = Math.min(vw / aw, vh / ah);
        setLayout({
          scale,
          tx: -settings.areaX * scale,
          ty: -settings.areaY * scale,
        });
      } else {
        // STAGE mode — full 1920×1080 fits the viewport.
        const scale = Math.min(vw / STAGE_W, vh / STAGE_H);
        setLayout({ scale, tx: 0, ty: 0 });
      }
    };
    recalc();
    window.addEventListener("resize", recalc);
    return () => window.removeEventListener("resize", recalc);
  }, [wantsUnitScale, wantsWidthFit, settings.areaX, settings.areaY, settings.areaW, settings.areaH]);

  return (
    <div style={{
      position: "fixed",
      inset: 0,
      overflow: "hidden",
      background: "transparent",
    }}>
      {/* Wrapper sized to the scaled stage. We position it via `left/top`
          so the area aligns to the viewport top-left in AREA mode.
          CaptionRenderer applies the scale internally — we DON'T add
          another CSS transform here, otherwise scales compound. */}
      <div style={{
        position: "absolute",
        left:  layout.tx,
        top:   layout.ty,
        width:  STAGE_W * layout.scale,
        height: STAGE_H * layout.scale,
      }}>
        <CaptionRenderer stageScale={layout.scale} showGuides={false} />
      </div>
    </div>
  );
}

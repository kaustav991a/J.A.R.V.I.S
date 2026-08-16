import React, { useEffect, useRef } from "react";
import gsap from "gsap";

const HudReticle = React.memo(function HudReticle() {
  const ring1Ref = useRef(null);
  const ring2Ref = useRef(null);
  const ring3Ref = useRef(null);

  useEffect(() => {
    // GSAP infinite rotations — no "jump" because repeat: -1 is seamless.
    //
    // Review batch 12, 2026-08-16: these were fire-and-forget. `repeat: -1`
    // tweens never complete, so GSAP holds them on its global ticker — and a
    // reference to the DOM node — for the life of the page. Nothing killed them
    // on unmount, so every open/close of a view carrying the reticle left three
    // more immortal tweens animating detached elements. The ticker cost grows
    // for the whole session and the nodes can never be collected.
    const tweens = [
      gsap.to(ring1Ref.current, {
        rotation: 360,
        duration: 10,
        repeat: -1,
        ease: "none",
      }),
      gsap.to(ring2Ref.current, {
        rotation: -360,
        duration: 15,
        repeat: -1,
        ease: "none",
      }),
      gsap.to(ring3Ref.current, {
        rotation: 360,
        duration: 25,
        repeat: -1,
        ease: "none",
      }),
    ];
    return () => tweens.forEach((t) => t.kill());
  }, []);

  return (
    <div className="hud-reticle">
      <div className="ring ring-1" ref={ring1Ref}></div>
      <div className="ring ring-2" ref={ring2Ref}></div>
      <div className="ring ring-3" ref={ring3Ref}></div>
    </div>
  );
});

export default HudReticle;

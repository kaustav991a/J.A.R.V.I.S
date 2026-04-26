import React, { useEffect, useRef } from "react";
import gsap from "gsap";

const HudReticle = () => {
  const ring1Ref = useRef(null);
  const ring2Ref = useRef(null);
  const ring3Ref = useRef(null);

  useEffect(() => {
    // GSAP infinite rotations — no "jump" because repeat: -1 is seamless
    gsap.to(ring1Ref.current, {
      rotation: 360,
      duration: 10,
      repeat: -1,
      ease: "none",
    });

    gsap.to(ring2Ref.current, {
      rotation: -360,
      duration: 15,
      repeat: -1,
      ease: "none",
    });

    gsap.to(ring3Ref.current, {
      rotation: 360,
      duration: 25,
      repeat: -1,
      ease: "none",
    });
  }, []);

  return (
    <div className="hud-reticle">
      <div className="ring ring-1" ref={ring1Ref}></div>
      <div className="ring ring-2" ref={ring2Ref}></div>
      <div className="ring ring-3" ref={ring3Ref}></div>
    </div>
  );
};

export default HudReticle;

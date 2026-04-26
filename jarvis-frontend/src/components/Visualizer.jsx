import React, { useEffect, useRef } from "react";
import * as THREE from "three";

export default function Visualizer({ status }) {
  const mountRef = useRef(null);
  const statusRef = useRef(status);

  // Web Audio API Refs for your microphone
  const audioContextRef = useRef(null);
  const analyserRef = useRef(null);
  const dataArrayRef = useRef(null);
  const micVolumeRef = useRef(0);

  const targets = useRef({
    timeScale: 0.8,
    brightness: 1.2,
    scale: 0.2,
    rotationSpeedY: 0.005,
  });

  useEffect(() => {
    statusRef.current = status;
    let stream = null;

    if (status === "listening" || status === "waking") {
      navigator.mediaDevices
        .getUserMedia({ audio: true })
        .then((mediaStream) => {
          stream = mediaStream;
          audioContextRef.current = new (
            window.AudioContext || window.webkitAudioContext
          )();
          analyserRef.current = audioContextRef.current.createAnalyser();
          analyserRef.current.fftSize = 256;
          const source =
            audioContextRef.current.createMediaStreamSource(stream);
          source.connect(analyserRef.current);
          dataArrayRef.current = new Uint8Array(
            analyserRef.current.frequencyBinCount,
          );
        })
        .catch((err) => console.error("Mic access denied:", err));
    } else {
      if (
        audioContextRef.current &&
        audioContextRef.current.state !== "closed"
      ) {
        audioContextRef.current.close();
      }
      micVolumeRef.current = 0;
    }

    // --- PHASE 2: Cinematic State Targets ---
    switch (status) {
      case "booting":
        targets.current = {
          timeScale: 0.3,
          brightness: 0.5,
          scale: 0.12,
          rotationSpeedY: 0.002,
        };
        break;
      case "uplinking":
        targets.current = {
          timeScale: 0.8,
          brightness: 0.9,
          scale: 0.16,
          rotationSpeedY: 0.008,
        };
        break;
      case "uplink_established":
      case "sync":
        targets.current = {
          timeScale: 1.5,
          brightness: 1.5,
          scale: 0.22,
          rotationSpeedY: 0.015,
        };
        break;
      case "processing_llm":
      case "searching":
        targets.current = {
          timeScale: 2.5,
          brightness: 1.8,
          scale: 0.25,
          rotationSpeedY: 0.03,
        };
        break;
      case "executing":
      case "speaking":
        targets.current = {
          timeScale: 1.5,
          brightness: 2.0,
          scale: 0.2,
          rotationSpeedY: 0.01,
        };
        break;
      case "offline":
        targets.current = {
          timeScale: 0.05,
          brightness: 0.1,
          scale: 0.08,
          rotationSpeedY: 0.0005,
        };
        break;
      case "listening":
      default:
        // Idle Standby State
        targets.current = {
          timeScale: 0.6,
          brightness: 1.1,
          scale: 0.18,
          rotationSpeedY: 0.004,
        };
    }

    return () => {
      if (stream) stream.getTracks().forEach((track) => track.stop());
      if (
        audioContextRef.current &&
        audioContextRef.current.state !== "closed"
      ) {
        audioContextRef.current.close();
      }
    };
  }, [status]);

  useEffect(() => {
    const width = 300;
    const height = 300;

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(75, width / height, 0.1, 100);
    camera.position.z = 2.4;

    const renderer = new THREE.WebGLRenderer({
      canvas: mountRef.current,
      antialias: true,
      alpha: true,
    });
    renderer.setSize(width, height);
    renderer.setPixelRatio(1.0); // 1.0 limits the pixel density to prevent GPU/CPU spikes on high-res displays
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 0.9;

    const mainGroup = new THREE.Group();
    scene.add(mainGroup);

    // --- GLSL NOISE FUNCTIONS ---
    const noiseFunctions = `
        vec3 mod289(vec3 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
        vec4 mod289(vec4 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
        vec4 permute(vec4 x) { return mod289(((x*34.0)+1.0)*x); }
        vec4 taylorInvSqrt(vec4 r) { return 1.79284291400159 - 0.85373472095314 * r; }

        float snoise(vec3 v) {
            const vec2  C = vec2(1.0/6.0, 1.0/3.0) ;
            const vec4  D = vec4(0.0, 0.5, 1.0, 2.0);
            vec3 i  = floor(v + dot(v, C.yyy) );
            vec3 x0 = v - i + dot(i, C.xxx) ;
            vec3 g = step(x0.yzx, x0.xyz);
            vec3 l = 1.0 - g;
            vec3 i1 = min( g.xyz, l.zxy );
            vec3 i2 = max( g.xyz, l.zxy );
            vec3 x1 = x0 - i1 + C.xxx;
            vec3 x2 = x0 - i2 + C.yyy;
            vec3 x3 = x0 - D.yyy;
            i = mod289(i);
            vec4 p = permute( permute( permute(
                        i.z + vec4(0.0, i1.z, i2.z, 1.0 ))
                    + i.y + vec4(0.0, i1.y, i2.y, 1.0 ))
                    + i.x + vec4(0.0, i1.x, i2.x, 1.0 ));
            float n_ = 0.142857142857;
            vec3  ns = n_ * D.wyz - D.xzx;
            vec4 j = p - 49.0 * floor(p * ns.z * ns.z);
            vec4 x_ = floor(j * ns.z);
            vec4 y_ = floor(j - 7.0 * x_ );
            vec4 x = x_ *ns.x + ns.yyyy;
            vec4 y = y_ *ns.x + ns.yyyy;
            vec4 h = 1.0 - abs(x) - abs(y);
            vec4 b0 = vec4( x.xy, y.xy );
            vec4 b1 = vec4( x.zw, y.zw );
            vec4 s0 = floor(b0)*2.0 + 1.0;
            vec4 s1 = floor(b1)*2.0 + 1.0;
            vec4 sh = -step(h, vec4(0.0));
            vec4 a0 = b0.xzyw + s0.xzyw*sh.xxyy ;
            vec4 a1 = b1.xzyw + s1.xzyw*sh.zzww ;
            vec3 p0 = vec3(a0.xy,h.x);
            vec3 p1 = vec3(a0.zw,h.y);
            vec3 p2 = vec3(a1.xy,h.z);
            vec3 p3 = vec3(a1.zw,h.w);
            vec4 norm = taylorInvSqrt(vec4(dot(p0,p0), dot(p1,p1), dot(p2, p2), dot(p3,p3)));
            p0 *= norm.x; p1 *= norm.y; p2 *= norm.z; p3 *= norm.w;
            vec4 m = max(0.6 - vec4(dot(x0,x0), dot(x1,x1), dot(x2,x2), dot(x3,x3)), 0.0);
            m = m * m;
            return 42.0 * dot( m*m, vec4( dot(p0,x0), dot(p1,x1), dot(p2,x2), dot(p3,x3) ) );
        }

        float fbm(vec3 p) {
            float total = 0.0;
            float amplitude = 0.5;
            float frequency = 1.0;
            for (int i = 0; i < 3; i++) { 
                total += snoise(p * frequency) * amplitude;
                amplitude *= 0.5;
                frequency *= 2.0;
            }
            return total;
        }
    `;

    const pointLight = new THREE.PointLight(0x00ffcc, 2.0, 10);
    mainGroup.add(pointLight);

    // Optimized from 64x64 to 32x32 to drastically reduce vertex calculations
    const shellGeo = new THREE.SphereGeometry(1.0, 32, 32);
    const shellMat = new THREE.ShaderMaterial({
      vertexShader: `
            uniform float uSpike;
            uniform float uTime;
            varying vec3 vNormal;
            varying vec3 vViewPosition;
            ${noiseFunctions}
            void main() {
                vNormal = normalize(normalMatrix * normal);
                float noiseVal = fbm(position * 8.0 + uTime);
                float displacement = noiseVal * uSpike * 0.2; 
                vec3 newPosition = position + normal * displacement;
                
                vec4 mvPosition = modelViewMatrix * vec4(newPosition, 1.0);
                vViewPosition = -mvPosition.xyz;
                gl_Position = projectionMatrix * mvPosition;
            }
        `,
      fragmentShader: `
            varying vec3 vNormal;
            varying vec3 vViewPosition;
            uniform vec3 uColor;
            uniform float uOpacity;
            void main() {
                float fresnel = pow(1.0 - dot(normalize(vNormal), normalize(vViewPosition)), 2.5);
                gl_FragColor = vec4(uColor, fresnel * uOpacity);
            }
        `,
      uniforms: {
        uColor: { value: new THREE.Color(0x00ffcc) },
        uOpacity: { value: 0.5 },
        uTime: { value: 0 },
        uSpike: { value: 0 },
      },
      transparent: true,
      blending: THREE.AdditiveBlending,
      side: THREE.FrontSide,
      depthWrite: false,
    });

    const shellMesh = new THREE.Mesh(shellGeo, shellMat);
    mainGroup.add(shellMesh);

    // --- PLASMA (Core) ---
    // Optimized from 64x64 to 32x32
    const plasmaGeo = new THREE.SphereGeometry(0.998, 32, 32);
    const plasmaMat = new THREE.ShaderMaterial({
      uniforms: {
        uTime: { value: 0 },
        uScale: { value: 0.2 },
        uBrightness: { value: 1.31 },
        uThreshold: { value: 0.09 },
        uSpike: { value: 0 },
        uColorDeep: { value: new THREE.Color(0x020b12) },
        uColorMid: { value: new THREE.Color(0x0084ff) },
        uColorBright: { value: new THREE.Color(0x00ffcc) },
      },
      vertexShader: `
            uniform float uSpike;
            uniform float uTime;
            varying vec3 vPosition;
            varying vec3 vNormal;
            varying vec3 vViewPosition;
            ${noiseFunctions}
            void main() {
                vNormal = normalize(normalMatrix * normal);
                float noiseVal = fbm(position * 6.0 - uTime);
                float displacement = noiseVal * uSpike * 0.12; 
                vec3 newPosition = position + normal * displacement;
                
                vPosition = newPosition; 
                vec4 mvPosition = modelViewMatrix * vec4(newPosition, 1.0);
                vViewPosition = -mvPosition.xyz; 
                gl_Position = projectionMatrix * mvPosition;
            }
        `,
      fragmentShader: `
            uniform float uTime;
            uniform float uScale;
            uniform float uBrightness;
            uniform float uThreshold;
            uniform vec3 uColorDeep;
            uniform vec3 uColorMid;
            uniform vec3 uColorBright;
            varying vec3 vPosition;
            varying vec3 vNormal;
            varying vec3 vViewPosition;
            ${noiseFunctions}
            void main() {
                vec3 p = vPosition * uScale; 
                vec3 q = vec3(
                    fbm(p + vec3(0.0, uTime * 0.05, 0.0)),
                    fbm(p + vec3(5.2, 1.3, 2.8) + uTime * 0.05),
                    fbm(p + vec3(2.2, 8.4, 0.5) - uTime * 0.02)
                );
                float density = fbm(p + 2.0 * q);
                float t = (density + 0.4) * 0.8;
                float alpha = smoothstep(uThreshold, 0.7, t);
                vec3 cWhite = vec3(1.0, 1.0, 1.0);
                vec3 color = mix(uColorDeep, uColorMid, smoothstep(uThreshold, 0.5, t));
                color = mix(color, uColorBright, smoothstep(0.5, 0.8, t));
                color = mix(color, cWhite, smoothstep(0.8, 1.0, t));
                float facing = dot(normalize(vNormal), normalize(vViewPosition));
                float depthFactor = (facing + 1.0) * 0.5;
                float finalAlpha = alpha * (0.02 + 0.98 * depthFactor);
                gl_FragColor = vec4(color * uBrightness, finalAlpha);
            }
        `,
      transparent: true,
      blending: THREE.AdditiveBlending,
      side: THREE.DoubleSide,
      depthWrite: false,
    });

    const plasmaMesh = new THREE.Mesh(plasmaGeo, plasmaMat);
    mainGroup.add(plasmaMesh);

    // --- PARTICLES ---
    const pCount = 150;
    const pPos = new Float32Array(pCount * 3);
    const pSizes = new Float32Array(pCount);
    for (let i = 0; i < pCount; i++) {
      const r = 0.95 * Math.cbrt(Math.random());
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(2 * Math.random() - 1);
      pPos[i * 3] = r * Math.sin(phi) * Math.cos(theta);
      pPos[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
      pPos[i * 3 + 2] = r * Math.cos(phi);
      pSizes[i] = Math.random();
    }
    const pGeo = new THREE.BufferGeometry();
    pGeo.setAttribute("position", new THREE.BufferAttribute(pPos, 3));
    pGeo.setAttribute("aSize", new THREE.BufferAttribute(pSizes, 1));

    const pMat = new THREE.ShaderMaterial({
      uniforms: {
        uTime: { value: 0 },
        uColor: { value: new THREE.Color(0x00ffff) },
      },
      vertexShader: `
            uniform float uTime;
            attribute float aSize;
            varying float vAlpha;
            void main() {
                vec3 pos = position;
                pos *= 1.2; 
                pos.y += sin(uTime * 0.5 + pos.x) * 0.05;
                vec4 mvPosition = modelViewMatrix * vec4(pos, 1.0);
                gl_Position = projectionMatrix * mvPosition;
                gl_PointSize = (12.0 * aSize + 4.0) * (1.0 / -mvPosition.z); 
                vAlpha = 0.8 + 0.2 * sin(uTime + aSize * 10.0);
            }
        `,
      fragmentShader: `
            uniform vec3 uColor;
            varying float vAlpha;
            void main() {
                vec2 uv = gl_PointCoord - vec2(0.5);
                float dist = length(uv);
                if(dist > 0.5) discard;
                float glow = pow(1.0 - (dist * 2.0), 1.2); 
                gl_FragColor = vec4(uColor, glow * vAlpha * 2.0); 
            }
        `,
      transparent: true,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    });
    mainGroup.add(new THREE.Points(pGeo, pMat));

    // --- ANIMATION LOOP ---
    let currentParams = {
      timeScale: 0.1,
      brightness: 0.2,
      scale: 0.1,
      rotationSpeedY: 0.001,
    };
    let currentSpike = 0;
    let animationFrameId;
    const clock = new THREE.Clock();
    let lastTime = 0;
    const targetFPS = 30;
    const frameDelay = 1000 / targetFPS;

    function animate(time) {
      animationFrameId = requestAnimationFrame(animate);
      
      // FPS Throttle
      if (time - lastTime < frameDelay) return;
      lastTime = time;

      const t = clock.getElapsedTime();

      let volumeBoost = 0;

      if (
        (statusRef.current === "listening" || statusRef.current === "waking") &&
        analyserRef.current &&
        dataArrayRef.current
      ) {
        analyserRef.current.getByteFrequencyData(dataArrayRef.current);
        let sum = 0;
        for (let i = 0; i < dataArrayRef.current.length; i++) {
          sum += dataArrayRef.current[i];
        }
        const avg = sum / dataArrayRef.current.length;
        micVolumeRef.current = avg / 255.0;

        if (micVolumeRef.current > 0.02) {
          volumeBoost = Math.min(micVolumeRef.current * 7.0, 1.8);
        }
      } else if (
        statusRef.current === "executing" ||
        statusRef.current === "speaking"
      ) {
        const fakeWave =
          (Math.sin(t * 35) * 0.5 + 0.5) * (Math.sin(t * 18) * 0.5 + 0.5);
        if (fakeWave > 0.3) {
          volumeBoost = fakeWave * 1.5;
        }
      }

      // Slower lerp for spike prevents violent snapping
      currentSpike = THREE.MathUtils.lerp(currentSpike, volumeBoost, 0.1);

      // --- SMOOTH SCALING FIX ---
      // These lerp values are slightly lower to ensure the transition between offline/listening isn't jarring
      currentParams.timeScale = THREE.MathUtils.lerp(
        currentParams.timeScale,
        targets.current.timeScale,
        0.03,
      );
      currentParams.scale = THREE.MathUtils.lerp(
        currentParams.scale,
        targets.current.scale,
        0.03,
      );
      currentParams.rotationSpeedY = THREE.MathUtils.lerp(
        currentParams.rotationSpeedY,
        targets.current.rotationSpeedY,
        0.03,
      );
      currentParams.brightness = THREE.MathUtils.lerp(
        currentParams.brightness,
        targets.current.brightness + currentSpike * 2.0,
        0.08,
      );

      shellMat.uniforms.uTime.value = t * currentParams.timeScale;
      shellMat.uniforms.uSpike.value = currentSpike;

      plasmaMat.uniforms.uTime.value = t * currentParams.timeScale;
      plasmaMat.uniforms.uBrightness.value = currentParams.brightness;
      plasmaMat.uniforms.uScale.value = currentParams.scale;
      plasmaMat.uniforms.uSpike.value = currentSpike;

      plasmaMesh.rotation.y = t * 0.08;
      mainGroup.rotation.y += currentParams.rotationSpeedY;
      mainGroup.rotation.x += 0.002;

      renderer.render(scene, camera);
    }

    requestAnimationFrame(animate);

    return () => {
      cancelAnimationFrame(animationFrameId);
      scene.clear();
      shellGeo.dispose();
      shellMat.dispose();
      plasmaGeo.dispose();
      plasmaMat.dispose();
      pGeo.dispose();
      pMat.dispose();
      renderer.dispose();
    };
  }, []);

  return <canvas ref={mountRef} style={{ width: "300px", height: "300px" }} />;
}

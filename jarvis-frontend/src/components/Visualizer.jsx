import React, { useEffect, useRef } from "react";
import * as THREE from "three";

export default function Visualizer({ status }) {
  const mountRef = useRef(null);

  // 1. THE FIX: We use a ref to track status inside the Three.js animation loop
  const statusRef = useRef(status);

  // Web Audio API Refs for your microphone
  const audioContextRef = useRef(null);
  const analyserRef = useRef(null);
  const dataArrayRef = useRef(null);
  const micVolumeRef = useRef(0);

  // This ref holds the base animation targets
  const targets = useRef({
    timeScale: 0.8,
    brightness: 1.2,
    scale: 0.2, // Shader noise scale
    rotationSpeedY: 0.005,
  });

  // Update targets and microphone state whenever the status changes
  useEffect(() => {
    statusRef.current = status;

    let stream = null;

    // Handle Microphone access when listening
    if (status === "listening") {
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
      // Close mic when not listening to save resources
      if (
        audioContextRef.current &&
        audioContextRef.current.state !== "closed"
      ) {
        audioContextRef.current.close();
      }
      micVolumeRef.current = 0;
    }

    // Set Three.js Base Targets
    switch (status) {
      case "processing_llm": // Thinking
        targets.current = {
          timeScale: 2.5,
          brightness: 2.0,
          scale: 0.35,
          rotationSpeedY: 0.03,
        };
        break;
      case "executing": // J.A.R.V.I.S. is Talking
        targets.current = {
          timeScale: 1.8,
          brightness: 2.5,
          scale: 0.25,
          rotationSpeedY: 0.01,
        };
        break;
      case "offline": // Dead
        targets.current = {
          timeScale: 0.1,
          brightness: 0.2,
          scale: 0.1,
          rotationSpeedY: 0.001,
        };
        break;
      default: // 'online' / Idle / Listening
        targets.current = {
          timeScale: 0.8,
          brightness: 1.2,
          scale: 0.2,
          rotationSpeedY: 0.005,
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
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 0.9;

    const mainGroup = new THREE.Group();
    scene.add(mainGroup);

    // --- GLSL NOISE FUNCTIONS (Unchanged) ---
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

    // 2. LIGHTS
    const pointLight = new THREE.PointLight(0x00ffcc, 2.0, 10);
    mainGroup.add(pointLight);

    // 3. OUTER SHELL (Glass)
    const shellGeo = new THREE.SphereGeometry(1.0, 64, 64);
    const shellShader = {
      vertexShader: `
            varying vec3 vNormal;
            varying vec3 vViewPosition;
            void main() {
                vNormal = normalize(normalMatrix * normal);
                vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
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
    };

    const shellBackMat = new THREE.ShaderMaterial({
      vertexShader: shellShader.vertexShader,
      fragmentShader: shellShader.fragmentShader,
      uniforms: {
        uColor: { value: new THREE.Color(0x002233) },
        uOpacity: { value: 0.3 },
      },
      transparent: true,
      blending: THREE.AdditiveBlending,
      side: THREE.BackSide,
      depthWrite: false,
    });

    const shellFrontMat = new THREE.ShaderMaterial({
      vertexShader: shellShader.vertexShader,
      fragmentShader: shellShader.fragmentShader,
      uniforms: {
        uColor: { value: new THREE.Color(0x00ffcc) },
        uOpacity: { value: 0.4 },
      },
      transparent: true,
      blending: THREE.AdditiveBlending,
      side: THREE.FrontSide,
      depthWrite: false,
    });

    mainGroup.add(new THREE.Mesh(shellGeo, shellBackMat));
    mainGroup.add(new THREE.Mesh(shellGeo, shellFrontMat));

    // 4. PLASMA (Gas)
    const plasmaGeo = new THREE.SphereGeometry(0.998, 128, 128);
    const plasmaMat = new THREE.ShaderMaterial({
      uniforms: {
        uTime: { value: 0 },
        uScale: { value: 0.2 },
        uBrightness: { value: 1.31 },
        uThreshold: { value: 0.09 },
        uColorDeep: { value: new THREE.Color(0x020b12) },
        uColorMid: { value: new THREE.Color(0x0084ff) },
        uColorBright: { value: new THREE.Color(0x00ffcc) },
      },
      vertexShader: `
            varying vec3 vPosition;
            varying vec3 vNormal;
            varying vec3 vViewPosition;
            void main() {
                vPosition = position;
                vNormal = normalize(normalMatrix * normal);
                vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
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

    // 5. PARTICLES
    const pCount = 300;
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
        uColor: { value: new THREE.Color(0x00ffcc) },
      },
      vertexShader: `
            uniform float uTime;
            attribute float aSize;
            varying float vAlpha;
            void main() {
                vec3 pos = position;
                pos.y += sin(uTime * 0.2 + pos.x) * 0.02;
                pos.x += cos(uTime * 0.15 + pos.z) * 0.02;
                vec4 mvPosition = modelViewMatrix * vec4(pos, 1.0);
                gl_Position = projectionMatrix * mvPosition;
                gl_PointSize = (8.0 * aSize + 4.0) * (1.0 / -mvPosition.z);
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
                float glow = pow(1.0 - (dist * 2.0), 1.8);
                gl_FragColor = vec4(uColor, glow * vAlpha);
            }
        `,
      transparent: true,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    });
    mainGroup.add(new THREE.Points(pGeo, pMat));

    // 6. ANIMATION LOOP WITH AUDIO REACTIVITY
    const clock = new THREE.Clock();
    let currentParams = {
      timeScale: 0.8,
      brightness: 1.2,
      scale: 0.2,
      rotationSpeedY: 0.005,
    };
    let animationFrameId;

    function animate() {
      animationFrameId = requestAnimationFrame(animate);
      const t = clock.getElapsedTime();

      // --- VOLUME LOGIC ---
      let volumeBoost = 0;

      // 1. React to YOUR voice when listening
      if (
        statusRef.current === "listening" &&
        analyserRef.current &&
        dataArrayRef.current
      ) {
        analyserRef.current.getByteFrequencyData(dataArrayRef.current);
        let sum = 0;
        for (let i = 0; i < dataArrayRef.current.length; i++) {
          sum += dataArrayRef.current[i];
        }
        // Normalize 0 to 1
        const avg = sum / dataArrayRef.current.length;
        micVolumeRef.current = avg / 255.0;
        volumeBoost = micVolumeRef.current;
      }
      // 2. React to J.A.R.V.I.S.'s voice when executing
      else if (statusRef.current === "executing") {
        // We use a math wave to fake a voice waveform so the blob stutters beautifully
        const fakeWave =
          (Math.sin(t * 30) * 0.5 + 0.5) * (Math.sin(t * 12) * 0.5 + 0.5);
        volumeBoost = fakeWave * 0.8;
      }

      // Smoothly Lerp base parameters
      currentParams.timeScale = THREE.MathUtils.lerp(
        currentParams.timeScale,
        targets.current.timeScale,
        0.05,
      );
      currentParams.scale = THREE.MathUtils.lerp(
        currentParams.scale,
        targets.current.scale,
        0.05,
      );
      currentParams.rotationSpeedY = THREE.MathUtils.lerp(
        currentParams.rotationSpeedY,
        targets.current.rotationSpeedY,
        0.05,
      );

      // Spike the brightness instantly with the volume
      currentParams.brightness = THREE.MathUtils.lerp(
        currentParams.brightness,
        targets.current.brightness + volumeBoost * 3.0, // Glows fiercely when speaking
        0.15,
      );

      // Apply to shaders
      plasmaMat.uniforms.uTime.value = t * currentParams.timeScale;
      plasmaMat.uniforms.uBrightness.value = currentParams.brightness;
      plasmaMat.uniforms.uScale.value = currentParams.scale;
      pMat.uniforms.uTime.value = t;

      // PHYSICAL BOUNCE: Scale the actual 3D geometry up with the voice
      const targetPhysicalScale = 1.0 + volumeBoost * 0.35; // Bounces 35% larger
      mainGroup.scale.lerp(
        new THREE.Vector3(
          targetPhysicalScale,
          targetPhysicalScale,
          targetPhysicalScale,
        ),
        0.2,
      );

      // Apply Rotation
      plasmaMesh.rotation.y = t * 0.08;
      mainGroup.rotation.y += currentParams.rotationSpeedY;
      mainGroup.rotation.x += 0.002;

      renderer.render(scene, camera);
    }

    animate();

    return () => {
      cancelAnimationFrame(animationFrameId);
      // Removed the error-prone node removal. React handles canvas destruction.
      scene.clear();
      renderer.dispose();
    };
  }, []);

  return <canvas ref={mountRef} style={{ width: "300px", height: "300px" }} />;
}

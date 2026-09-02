"use client";

import {
  Suspense,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { Center, useAnimations, useGLTF } from "@react-three/drei";
import { Canvas, useFrame } from "@react-three/fiber";
import { Group, Mesh, MeshStandardMaterial } from "three";
import { clone } from "three/examples/jsm/utils/SkeletonUtils.js";

import type { ProgressStatus } from "@/lib/workflow-progress";

const BULL_MODEL = "/models/bull-quaternius.gltf";

function useReducedMotion() {
  const [reduced, setReduced] = useState(false);

  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);

  return reduced;
}

function BullModel({
  status,
  reducedMotion,
  onReady,
}: {
  status: ProgressStatus;
  reducedMotion: boolean;
  onReady: () => void;
}) {
  const root = useRef<Group>(null);
  const { scene, animations } = useGLTF(BULL_MODEL);
  const model = useMemo(() => {
    const instance = clone(scene);
    instance.traverse((child) => {
      if (child instanceof Mesh) {
        child.material = new MeshStandardMaterial({
          color: "#ffb43f",
          emissive: "#ff7900",
          emissiveIntensity: 1.3,
          metalness: 0.18,
          roughness: 0.32,
          transparent: true,
          opacity: 0.94,
        });
      }
    });
    return instance;
  }, [scene]);
  const { actions } = useAnimations(animations, root);

  useEffect(() => {
    onReady();
  }, [onReady]);

  useEffect(() => {
    const clip = reducedMotion
      ? "Idle"
      : status === "active"
        ? "Gallop"
        : status === "failed"
          ? "Idle_HitReact1"
          : status === "complete"
            ? "Idle_2"
            : "Idle";
    const action = actions[clip];
    action?.reset().fadeIn(0.22).play();
    return () => {
      action?.fadeOut(0.18);
    };
  }, [actions, reducedMotion, status]);

  useFrame(({ clock }) => {
    if (!root.current || reducedMotion || status !== "active") return;
    root.current.rotation.y = Math.sin(clock.elapsedTime * 2.4) * 0.055;
  });

  return (
    <group ref={root} rotation={[0, Math.PI, 0]}>
      <Center>
        <primitive object={model} scale={0.66} />
      </Center>
    </group>
  );
}

function AnimatedAgentFrame({
  ready,
  fallbackClass,
  lightColor,
  children,
}: {
  ready: boolean;
  fallbackClass: string;
  lightColor: string;
  children: ReactNode;
}) {
  const fallback = <span className={`hologram-render ${fallbackClass}`} aria-hidden="true" />;

  return (
    <span
      aria-hidden="true"
      style={{ display: "block", position: "relative", zIndex: 2, width: 70, height: 70 }}
    >
      <span style={{ position: "absolute", inset: 1, opacity: ready ? 0 : 1, transition: "opacity .2s" }}>
        {fallback}
      </span>
      <Canvas
        orthographic
        camera={{ position: [0, 0, 10], zoom: 20 }}
        dpr={[1, 1.5]}
        fallback={fallback}
        gl={{ alpha: true, antialias: true, powerPreference: "low-power" }}
        style={{ position: "absolute", inset: 0, opacity: ready ? 1 : 0, transition: "opacity .22s" }}
      >
        <ambientLight intensity={1.4} />
        <pointLight position={[2, 3, 5]} color={lightColor} intensity={9} />
        <Suspense fallback={null}>{children}</Suspense>
      </Canvas>
    </span>
  );
}

export function AnimatedBullAgent({ status }: { status: ProgressStatus }) {
  const reducedMotion = useReducedMotion();
  const [ready, setReady] = useState(false);
  const markReady = useMemo(() => () => setReady(true), []);
  return (
    <AnimatedAgentFrame ready={ready} fallbackClass="render-bull" lightColor="#ffb43f">
      <BullModel status={status} reducedMotion={reducedMotion} onReady={markReady} />
    </AnimatedAgentFrame>
  );
}

useGLTF.preload(BULL_MODEL);

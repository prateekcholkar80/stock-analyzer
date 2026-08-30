export type NeuralSoundCue =
  | "activation"
  | "dispatch"
  | "evidence"
  | "verdict"
  | "complete"
  | "fault";

const MINIMUM_CUE_INTERVAL_MS: Readonly<Record<NeuralSoundCue, number>> = {
  activation: 900,
  dispatch: 650,
  evidence: 700,
  verdict: 1_200,
  complete: 1_200,
  fault: 1_500,
};

type AudioWindow = Window & typeof globalThis & {
  webkitAudioContext?: typeof AudioContext;
};

export class NeuralAudioEngine {
  private context: AudioContext | null = null;
  private master: GainNode | null = null;
  private enabled = false;
  private lastPlayed = new Map<NeuralSoundCue, number>();

  async setEnabled(enabled: boolean) {
    this.enabled = enabled;
    if (!enabled) {
      this.master?.gain.setTargetAtTime(0, this.context?.currentTime ?? 0, 0.02);
      return;
    }
    const context = await this.ensureContext();
    if (!context) return;
    this.master?.gain.setTargetAtTime(0.72, context.currentTime, 0.025);
    this.play("activation", true);
  }

  play(cue: NeuralSoundCue, bypassRateLimit = false) {
    if (!this.enabled) return;
    const now = Date.now();
    if (!bypassRateLimit && now - (this.lastPlayed.get(cue) ?? 0) < MINIMUM_CUE_INTERVAL_MS[cue]) return;
    this.lastPlayed.set(cue, now);
    void this.ensureContext().then((context) => {
      if (!context || !this.master) return;
      const start = context.currentTime + 0.01;
      const note = (frequency: number, offset: number, duration: number, gain: number, endFrequency = frequency) => {
        const oscillator = context.createOscillator();
        const envelope = context.createGain();
        oscillator.type = "sine";
        oscillator.frequency.setValueAtTime(frequency, start + offset);
        oscillator.frequency.exponentialRampToValueAtTime(Math.max(40, endFrequency), start + offset + duration);
        envelope.gain.setValueAtTime(0.0001, start + offset);
        envelope.gain.exponentialRampToValueAtTime(gain, start + offset + Math.min(0.035, duration / 3));
        envelope.gain.exponentialRampToValueAtTime(0.0001, start + offset + duration);
        oscillator.connect(envelope).connect(this.master!);
        oscillator.start(start + offset);
        oscillator.stop(start + offset + duration + 0.02);
      };

      if (cue === "activation") {
        note(145, 0, 0.55, 0.09, 310);
        note(420, 0.18, 0.42, 0.045, 690);
      } else if (cue === "dispatch") {
        note(510, 0, 0.11, 0.045, 710);
        note(760, 0.12, 0.1, 0.035, 930);
      } else if (cue === "evidence") {
        note(330, 0, 0.16, 0.04, 390);
        note(495, 0.1, 0.18, 0.04, 585);
      } else if (cue === "verdict") {
        note(294, 0, 0.28, 0.045);
        note(440, 0.13, 0.3, 0.045);
        note(587, 0.28, 0.38, 0.05);
      } else if (cue === "complete") {
        note(523, 0, 0.2, 0.04);
        note(659, 0.12, 0.22, 0.045);
        note(784, 0.24, 0.34, 0.05);
      } else {
        note(235, 0, 0.42, 0.055, 118);
        note(176, 0.12, 0.36, 0.035, 92);
      }
    });
  }

  async dispose() {
    this.enabled = false;
    const context = this.context;
    this.context = null;
    this.master = null;
    this.lastPlayed.clear();
    if (context && context.state !== "closed") await context.close();
  }

  private async ensureContext() {
    if (typeof window === "undefined") return null;
    if (!this.context) {
      const AudioContextConstructor = window.AudioContext || (window as AudioWindow).webkitAudioContext;
      if (!AudioContextConstructor) return null;
      this.context = new AudioContextConstructor();
      this.master = this.context.createGain();
      this.master.gain.value = 0.72;
      this.master.connect(this.context.destination);
    }
    if (this.context.state === "suspended") await this.context.resume();
    return this.context;
  }
}

// Captures microphone samples losslessly, starting on an exact audio frame so the take
// lines up with the reference playing in the headphones.
class Recorder extends AudioWorkletProcessor {
  constructor() {
    super();
    this.startFrame = Infinity;
    this.on = false;
    this.port.onmessage = (event) => {
      if (event.data.type === "start") {
        this.startFrame = event.data.frame;
        this.on = true;
      } else if (event.data.type === "stop") {
        this.on = false;
        this.port.postMessage({ type: "done" });
      }
    };
  }

  process(inputs) {
    const input = inputs[0];
    if (this.on && input.length && currentFrame + input[0].length > this.startFrame) {
      const skip = Math.max(0, this.startFrame - currentFrame);
      this.port.postMessage({ type: "chunk", data: input.map((channel) => channel.slice(skip)) });
    }
    return true;
  }
}

registerProcessor("recorder", Recorder);

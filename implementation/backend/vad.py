"""Bounded local energy endpointer for negotiated 60 ms PCM frames.

This is a deterministic baseline, not a trained speech detector or AEC. Real
microphone noise and wake-word behavior still require hardware calibration.
"""
import array


class EnergyVad:
    def __init__(self,threshold=500,minimum_speech_frames=2,silence_frames=10,idle_frames=84):
        self.threshold=threshold
        self.minimum_speech_frames=minimum_speech_frames
        self.silence_frames=silence_frames
        self.idle_frames=idle_frames
        self.speech=0;self.silence=0;self.total=0

    def voiced(self,pcm):
        samples=array.array("h",pcm)
        return sum(x*x for x in samples)>=len(samples)*self.threshold*self.threshold

    def feed(self,pcm):
        voiced=self.voiced(pcm)
        self.total+=1
        if voiced:self.speech+=1;self.silence=0
        else:self.silence+=1
        return ((self.speech>=self.minimum_speech_frames and self.silence>=self.silence_frames)
                or (self.speech<self.minimum_speech_frames and self.total>=self.idle_frames))

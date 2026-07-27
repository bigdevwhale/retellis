'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import type { Lang } from './i18n';

type SpeechRecognitionResultLike = {
  isFinal: boolean;
  0: { transcript: string };
};

type SpeechRecognitionEventLike = {
  resultIndex: number;
  results: { length: number; [index: number]: SpeechRecognitionResultLike };
};

type SpeechRecognitionLike = {
  lang: string;
  interim: boolean;
  continuous: boolean;
  onstart: (() => void) | null;
  onresult: ((e: SpeechRecognitionEventLike) => void) | null;
  onend: (() => void) | null;
  onerror: (() => void) | null;
  start: () => void;
  stop: () => void;
};

function getSR(): { new (): SpeechRecognitionLike } | null {
  if (typeof window === 'undefined') return null;
  const w = window as unknown as {
    SpeechRecognition?: { new (): SpeechRecognitionLike };
    webkitSpeechRecognition?: { new (): SpeechRecognitionLike };
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export function useSpeech(lang: Lang) {
  const [supported] = useState(
    () => getSR() !== null && typeof window !== 'undefined' && 'speechSynthesis' in window,
  );
  const [listening, setListening] = useState(false);
  const [interim, setInterim] = useState('');
  const [autoSpeak, setAutoSpeak] = useState(false);
  const recogRef = useRef<SpeechRecognitionLike | null>(null);
  const onFinalRef = useRef<((text: string) => void) | null>(null);
  const langRef = useRef(lang);
  useEffect(() => {
    langRef.current = lang;
  }, [lang]);

  const speak = useCallback((text: string) => {
    if (typeof window === 'undefined' || !window.speechSynthesis) return;
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.lang = langRef.current === 'ru' ? 'ru-RU' : 'en-US';
    u.rate = 0.95;
    u.pitch = 1.0;
    window.speechSynthesis.speak(u);
  }, []);

  const stopListen = useCallback(() => {
    recogRef.current?.stop();
  }, []);

  const startListen = useCallback((onFinal: (text: string) => void) => {
    const SR = getSR();
    if (!SR) return;
    onFinalRef.current = onFinal;
    const recog = new SR();
    recog.lang = langRef.current === 'ru' ? 'ru-RU' : 'en-US';
    recog.interim = true;
    recog.continuous = false;
    recog.onstart = () => setListening(true);
    recog.onresult = (e: SpeechRecognitionEventLike) => {
      let finalText = '';
      let interimText = '';
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        if (!r) continue;
        if (r.isFinal) finalText += r[0]?.transcript ?? '';
        else interimText += r[0]?.transcript ?? '';
      }
      setInterim(finalText || interimText);
      if (finalText && onFinalRef.current) onFinalRef.current(finalText);
    };
    recog.onend = () => {
      setListening(false);
      setInterim('');
    };
    recog.onerror = () => {
      setListening(false);
      setInterim('');
    };
    recogRef.current = recog;
    recog.start();
  }, []);

  const toggleAutoSpeak = useCallback(() => {
    setAutoSpeak((p) => {
      const next = !p;
      if (next && typeof window !== 'undefined' && window.speechSynthesis)
        window.speechSynthesis.cancel();
      return next;
    });
  }, []);

  useEffect(() => {
    return () => {
      recogRef.current?.stop();
      if (typeof window !== 'undefined' && window.speechSynthesis) window.speechSynthesis.cancel();
    };
  }, []);

  return {
    supported,
    listening,
    interim,
    autoSpeak,
    startListen,
    stopListen,
    speak,
    toggleAutoSpeak,
  };
}

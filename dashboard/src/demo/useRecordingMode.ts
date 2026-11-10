import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

const key = 'agentguard.recording-mode';

export function useRecordingMode() {
  const [params] = useSearchParams();
  const override = params.get('demo');
  const [enabled, setEnabled] = useState(() => {
    if (override === '1' || override === '0') return override === '1';
    try {
      return sessionStorage.getItem(key) === '1';
    } catch {
      return false;
    }
  });

  useEffect(() => {
    if (override === '1' || override === '0') setEnabled(override === '1');
  }, [override]);

  useEffect(() => {
    const root = document.documentElement;
    root.classList.toggle('recording-mode', enabled);
    try {
      sessionStorage.setItem(key, enabled ? '1' : '0');
    } catch {
      // The in-memory preference still applies when storage is unavailable.
    }
    return () => root.classList.remove('recording-mode');
  }, [enabled]);
}

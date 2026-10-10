// Presenter mode: ~125% type, a slim live list (EventTable slim) instead of the full table and feed, fewer
// secondary panels. Toggle: P or the header button.
import { createContext, useCallback, useContext, useEffect, useState } from "react";

const KEY = "tripwire.presenter";

function readStored(): boolean {
  try {
    return localStorage.getItem(KEY) === "1";
  } catch {
    return false;
  }
}

export function usePresenterState() {
  const [on, setOn] = useState<boolean>(readStored);
  useEffect(() => {
    document.documentElement.classList.toggle("presenter", on);
    try {
      localStorage.setItem(KEY, on ? "1" : "0");
    } catch {
      /* storage blocked: still works for this page view */
    }
  }, [on]);
  const toggle = useCallback(() => setOn((v) => !v), []);
  return { on, toggle };
}

export type PresenterCtx = ReturnType<typeof usePresenterState>;
export const PresenterContext = createContext<PresenterCtx>({ on: false, toggle: () => {} });
export const usePresenter = () => useContext(PresenterContext);

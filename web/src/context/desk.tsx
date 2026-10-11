"use client";

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

import { api, type Brief, type Job, type TraceEvent, type WatchStatus } from "@/lib/api";

interface DeskValue {
  brief: Brief | null;
  job: Job | null;
  watchStatus: WatchStatus | null;
  events: TraceEvent[];
  error: string | null;
  /** A pass or a scoring run started from this desk is in progress. */
  busy: boolean;
  /** Another process (a watch, or a CLI command) holds the database. */
  writerBusy: boolean;
  refresh: () => Promise<void>;
  start: () => Promise<void>;
  startWatch: () => Promise<void>;
  stopWatch: () => Promise<void>;
  analyze: () => Promise<void>;
  /** A page started a job itself (one notice, or the whole book). The desk follows it to the end. */
  followJob: (job: Job) => void;
}

const DeskContext = createContext<DeskValue | null>(null);

const UNREACHABLE = "The desk API is not reachable. Start it with: chanakya serve";

function message(err: unknown, fallback: string): string {
  if (err instanceof TypeError) return UNREACHABLE;
  return err instanceof Error && err.message ? err.message : fallback;
}

export function DeskProvider({ children }: { children: ReactNode }) {
  const [brief, setBrief] = useState<Brief | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [watchStatus, setWatchStatus] = useState<WatchStatus | null>(null);
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [jobTick, setJobTick] = useState(0);

  const refresh = useCallback(async () => {
    setBrief(await api.brief());
    setError(null);
  }, []);

  // First load, and a slow retry while the API is down so the desk recovers on its own.
  useEffect(() => {
    let cancelled = false;
    let timer = 0;
    async function load() {
      try {
        await refresh();
      } catch (err) {
        if (cancelled) return;
        setError(message(err, UNREACHABLE));
        timer = window.setTimeout(load, 5000);
      }
    }
    load();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [refresh]);

  // The job this desk started. Poll fast while it runs, then refresh the book once.
  useEffect(() => {
    let cancelled = false;
    let timer = 0;
    async function tick() {
      try {
        const { job: next } = await api.job();
        if (cancelled) return;
        setJob(next);
        if (next?.status === "running") {
          timer = window.setTimeout(tick, 700);
          return;
        }
        if (next) await refresh();
      } catch {
        /* the brief loader reports that the API is down */
      }
    }
    tick();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [jobTick, refresh]);

  // Who holds the database. A watch can be started from a terminal as well as from here.
  useEffect(() => {
    let cancelled = false;
    let timer = 0;
    async function tick() {
      try {
        const next = await api.watch();
        if (!cancelled) setWatchStatus(next);
      } catch {
        /* the brief loader reports that the API is down */
      }
      if (!cancelled) timer = window.setTimeout(tick, 5000);
    }
    tick();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, []);

  const jobRunning = job?.status === "running";
  const watchRunning = !!watchStatus?.running && !jobRunning;

  // Narration lines, read from the run's event file so a CLI watch shows up too.
  useEffect(() => {
    if (!watchRunning && !jobRunning) return;
    let cancelled = false;
    let timer = 0;
    async function tick() {
      try {
        const next = await api.events();
        if (!cancelled) setEvents(next.events);
      } catch {
        /* keep the lines already shown */
      }
      if (!cancelled) timer = window.setTimeout(tick, 2000);
    }
    tick();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [watchRunning, jobRunning]);

  // Counts move while something is writing.
  useEffect(() => {
    const running = jobRunning || watchRunning || brief?.last_run?.status === "running";
    if (!running) return;
    const timer = window.setInterval(() => {
      refresh().catch(() => undefined);
    }, 15000);
    return () => window.clearInterval(timer);
  }, [jobRunning, watchRunning, brief?.last_run?.status, refresh]);

  const start = useCallback(async () => {
    setError(null);
    try {
      const { job: next } = await api.start();
      setJob(next);
      setJobTick((value) => value + 1);
    } catch (err) {
      setError(message(err, "Could not start the pass."));
    }
  }, []);

  const startWatch = useCallback(async () => {
    setError(null);
    try {
      await api.watchAction("start");
      setWatchStatus({
        running: true,
        mode: "watch",
        run_id: null,
        source_id: null,
        source_name: null,
        pages: null,
        notices_stored: null,
        started_at: null,
        pause_s: 600,
      });
    } catch (err) {
      setError(message(err, "Could not start the watch."));
    }
  }, []);

  const stopWatch = useCallback(async () => {
    setError(null);
    try {
      setWatchStatus(await api.watchAction("stop"));
    } catch (err) {
      setError(message(err, "Could not stop the watch."));
    }
  }, []);

  const analyze = useCallback(async () => {
    setError(null);
    try {
      const { job: next } = await api.analyze("changed");
      setJob(next);
      setJobTick((value) => value + 1);
    } catch (err) {
      setError(message(err, "Could not score the stored notices."));
    }
  }, []);

  const followJob = useCallback((next: Job) => {
    setJob(next);
    setJobTick((value) => value + 1);
  }, []);

  return (
    <DeskContext.Provider
      value={{
        brief,
        job,
        watchStatus,
        events,
        error,
        busy: jobRunning,
        writerBusy: watchRunning,
        refresh,
        start,
        startWatch,
        stopWatch,
        analyze,
        followJob,
      }}
    >
      {children}
    </DeskContext.Provider>
  );
}

export function useDesk() {
  const ctx = useContext(DeskContext);
  if (!ctx) throw new Error("useDesk must be used within DeskProvider");
  return ctx;
}

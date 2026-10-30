import { useCallback, useEffect, useRef, useState } from "react";
import { HostUnavailableError, type NativeClient } from "./native";
import { flushOutbox } from "./outbox";
import type { ClipStorage } from "./storage";
import type { HostInfo, Job, JobCounts, OutboxItem } from "./types";

export type HostState = "checking" | "ready" | "unavailable" | "error";

const ACTIVE_POLL_MS = 1500;
const IDLE_POLL_MS = 5000;

export interface QueueState {
  host: HostState;
  hostInfo: HostInfo | null;
  hostMessage: string | null;
  jobs: Job[];
  counts: JobCounts | null;
  outbox: OutboxItem[];
  stale: boolean;
  refresh: () => Promise<void>;
  setOutbox: (items: OutboxItem[]) => void;
}

/** Connects to the host, keeps the job list fresh, and delivers the offline outbox. */
export function useQueue(client: NativeClient, storage: ClipStorage): QueueState {
  const [host, setHost] = useState<HostState>("checking");
  const [hostInfo, setHostInfo] = useState<HostInfo | null>(null);
  const [hostMessage, setHostMessage] = useState<string | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [counts, setCounts] = useState<JobCounts | null>(null);
  const [outbox, setOutbox] = useState<OutboxItem[]>([]);
  const [stale, setStale] = useState(false);
  const connected = useRef(false);

  const refresh = useCallback(async () => {
    try {
      if (!connected.current) {
        setHostInfo(await client.ping());
        connected.current = true;
        const flushed = await flushOutbox(client, storage);
        setOutbox(flushed.remaining);
      }
      const result = await client.listJobs();
      setJobs(result.jobs);
      setCounts(result.counts);
      setHost("ready");
      setHostMessage(null);
      setStale(false);
      await storage.saveQueue(result.jobs, result.counts);
    } catch (error) {
      connected.current = false;
      const cached = await storage.queue();
      if (cached) {
        setJobs(cached.jobs);
        setCounts(cached.counts);
        setStale(true);
      }
      setOutbox(await storage.outbox());
      setHost(error instanceof HostUnavailableError ? "unavailable" : "error");
      setHostMessage(error instanceof Error ? error.message : String(error));
    }
  }, [client, storage]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const anyActive = jobs.some((j) => j.status === "downloading" || j.status === "processing");
  useEffect(() => {
    if (host !== "ready") return;
    const timer = setInterval(() => void refresh(), anyActive ? ACTIVE_POLL_MS : IDLE_POLL_MS);
    return () => clearInterval(timer);
  }, [host, anyActive, refresh]);

  return { host, hostInfo, hostMessage, jobs, counts, outbox, stale, refresh, setOutbox };
}

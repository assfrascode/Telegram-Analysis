import React from "react";
import { act, create } from "react-test-renderer";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RetentionPanel } from "./RetentionPanel";

let renderer;
let request;
const initial = {
  job_retention_days: null, upload_retention_days: 7, upload_expiry_hours: 24,
  jobs: [], uploads: [], job_count: 0, upload_count: 0, pending_deletions: 0,
};
const input = (id) => renderer.root.findByProps({ id });
const button = (label) => renderer.root.findAllByType("button").find((node) => node.children.includes(label));
const preview = () => renderer.root.findByType("form").props.onSubmit({ preventDefault() {} });
async function mount() {
  await act(async () => { renderer = create(<RetentionPanel request={request} showToast={vi.fn()} onSelectJob={vi.fn()} onRefreshJobs={vi.fn()} />); });
}

beforeEach(() => {
  vi.stubGlobal("window", { confirm: vi.fn(() => true) });
  request = vi.fn(async (path, config) => {
    if (!config) return initial;
    return { ...initial, ...config.body, job_count: 230 };
  });
});

afterEach(() => {
  if (renderer) act(() => renderer.unmount());
  renderer = null;
  vi.unstubAllGlobals();
});

describe("retention policy", () => {
  it("previews a proposed policy without saving and confirms total affected rows before saving", async () => {
    await mount();
    expect(button("Save retention policy").props.disabled).toBe(true);
    act(() => input("job-retention-days").props.onChange({ target: { value: "30" } }));
    expect(button("Save retention policy").props.disabled).toBe(true);
    await act(async () => preview());
    expect(request).toHaveBeenLastCalledWith("/jobs/retention/preview", { method: "POST", body: { job_retention_days: 30, upload_retention_days: 7 } });
    expect(request.mock.calls.some(([, config]) => config?.method === "PUT")).toBe(false);
    expect(button("Save retention policy").props.disabled).toBe(false);
    window.confirm.mockReturnValueOnce(false);
    await act(async () => button("Save retention policy").props.onClick());
    expect(request.mock.calls.some(([, config]) => config?.method === "PUT")).toBe(false);
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining("230 analyses"));
    await act(async () => button("Save retention policy").props.onClick());
    expect(request).toHaveBeenLastCalledWith("/jobs/retention", { method: "PUT", body: { job_retention_days: 30, upload_retention_days: 7 } });
  });

  it("invalidates a preview after editing and sends null when disabling retention", async () => {
    await mount();
    act(() => input("job-retention-days").props.onChange({ target: { value: "30" } }));
    await act(async () => preview());
    expect(button("Save retention policy").props.disabled).toBe(false);
    act(() => input("upload-retention-days").props.onChange({ target: { value: "" } }));
    expect(button("Save retention policy").props.disabled).toBe(true);
    await act(async () => preview());
    expect(request).toHaveBeenLastCalledWith("/jobs/retention/preview", { method: "POST", body: { job_retention_days: 30, upload_retention_days: null } });
  });

  it.each(["0", "1.5", "36501"])("rejects invalid retention %s before requesting a preview", async (value) => {
    await mount();
    act(() => input("job-retention-days").props.onChange({ target: { value } }));
    await act(async () => preview());
    expect(request).toHaveBeenCalledTimes(1);
    expect(renderer.root.findByProps({ role: "alert" }).children[0]).toContain("whole number");
  });

  it("shows a cleanup error and leaves saving disabled when a preview fails", async () => {
    await mount();
    act(() => input("job-retention-days").props.onChange({ target: { value: "30" } }));
    request.mockRejectedValueOnce(new Error("Storage unavailable"));
    await act(async () => preview());
    expect(renderer.root.findByProps({ role: "alert" }).children).toContain("Storage unavailable");
    expect(button("Save retention policy").props.disabled).toBe(true);
  });
});

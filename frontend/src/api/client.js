const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || "").replace(/\/$/, "");
const WS_BASE_URL = (import.meta.env.VITE_WS_BASE_URL || "").replace(/\/$/, "");

export function buildApiUrl(path) {
  if (/^https?:\/\//i.test(path)) return path;
  if (!API_BASE_URL) return path;
  return `${API_BASE_URL}${path.startsWith("/") ? path : `/${path}`}`;
}

export function buildWsUrl(path, ticket) {
  const suffix = `${path.startsWith("/") ? path : `/${path}`}${path.includes("?") ? "&" : "?"}ticket=${encodeURIComponent(ticket)}`;

  if (WS_BASE_URL) return `${WS_BASE_URL}${suffix}`;

  if (API_BASE_URL && /^https?:\/\//i.test(API_BASE_URL)) {
    const url = new URL(API_BASE_URL);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    return `${url.origin}${suffix}`;
  }

  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}${suffix}`;
}

export function authHeaders(token, extra = {}) {
  return {
    ...extra,
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

export async function apiJson(path, { token, method = "GET", body, headers = {} } = {}) {
  const requestHeaders = authHeaders(token, { "Content-Type": "application/json", ...headers });
  const response = await fetch(buildApiUrl(path), {
    method,
    headers: requestHeaders,
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`${response.status} ${detail}`);
  }

  if (response.status === 204) return null;
  return response.json();
}

export async function downloadBlob(path, { token } = {}) {
  const response = await fetch(buildApiUrl(path), { headers: authHeaders(token) });
  if (!response.ok) throw new Error(`${response.status} ${await response.text()}`);
  const contentDisposition = response.headers.get("Content-Disposition") || "";
  const encodedMatch = contentDisposition.match(/filename\*\s*=\s*UTF-8''([^;]+)/i);
  const quotedMatch = contentDisposition.match(/filename\s*=\s*"([^"]+)"/i);
  const plainMatch = contentDisposition.match(/filename\s*=\s*([^;\s]+)/i);
  let filename = quotedMatch?.[1] || plainMatch?.[1] || "report.zip";
  if (encodedMatch?.[1]) {
    try {
      filename = decodeURIComponent(encodedMatch[1].trim().replace(/^"|"$/g, ""));
    } catch {
      // Keep the ASCII fallback when an invalid extended filename is returned.
    }
  }
  filename = filename.replaceAll("\\", "/").split("/").pop() || "report.zip";
  return { blob: await response.blob(), filename };
}

export function uploadFileViaBackend(upload, file, token, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && onProgress) onProgress((event.loaded / event.total) * 100);
    };

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        onProgress?.(100);
        try {
          resolve(JSON.parse(xhr.responseText || "{}"));
        } catch {
          resolve({});
        }
      } else {
        reject(new Error(`${xhr.status} ${xhr.responseText}`));
      }
    };

    xhr.onerror = () => reject(new Error("Network error during upload"));
    xhr.onabort = () => reject(new Error("Upload was cancelled"));
    xhr.open("PUT", buildApiUrl(upload.backend_upload_url));
    if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    xhr.setRequestHeader("Content-Type", "application/zip");
    xhr.send(file);
  });
}

export async function uploadFileForAnalysis(upload, file, token, onProgress) {
  return uploadFileViaBackend(upload, file, token, onProgress);
}

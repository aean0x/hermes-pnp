#!/usr/bin/env node
// hermes-browser-ui-gate — static server for the @agent-infra/browser-ui
// page plus a CDP HTTP/WebSocket proxy to the persistent browser engine.
// Caddy fronts this on the public host; the gate itself stays loopback.
//
//   /json/*     -> CDP HTTP  (discover the browser WebSocket URL)
//   /devtools/* -> CDP WS    (browser + page endpoints, bidirectional)
//   /*          -> static    (index.html + bundle + app.js)
"use strict";

const http = require("node:http");
const net = require("node:net");
const fs = require("node:fs");
const path = require("node:path");

function arg(name, fallback) {
  const i = process.argv.indexOf(name);
  return i >= 0 && i + 1 < process.argv.length ? process.argv[i + 1] : fallback;
}

const LISTEN = arg("--listen", "127.0.0.1");
const PORT = parseInt(arg("--port", "4848"), 10);
const CDP_URL = new URL(arg("--cdp", "http://127.0.0.1:9222"));
const CDP_HOST = CDP_URL.hostname;
const CDP_PORT = parseInt(CDP_URL.port || "9222", 10);
const STATIC = path.resolve(arg("--static", "."));

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".map": "application/json",
  ".json": "application/json",
  ".svg": "image/svg+xml",
};

function proxyHttp(req, res) {
  const headers = { ...req.headers, host: CDP_HOST + ":" + CDP_PORT };
  delete headers["origin"];
  const upstream = http.request(
    {
      host: CDP_HOST,
      port: CDP_PORT,
      path: req.url,
      method: req.method,
      headers,
    },
    (up) => {
      res.writeHead(up.statusCode, up.headers);
      up.pipe(res);
    },
  );
  upstream.on("error", () => {
    if (!res.headersSent) res.writeHead(502, { "Content-Type": "text/plain" });
    res.end("CDP unavailable");
  });
  req.pipe(upstream);
}

function proxyWs(req, socket, head) {
  const upstream = net.connect(CDP_PORT, CDP_HOST);
  upstream.on("connect", () => {
    // Reconstruct the upgrade request and let Chromium negotiate 101 +
    // Sec-WebSocket-Accept itself; then pipe both directions unchanged.
    const headers = [];
    for (const [k, v] of Object.entries(req.headers)) {
      if (k === "host") continue;
      headers.push(k + ": " + v);
    }
    upstream.write(
      req.method + " " + req.url + " HTTP/1.1\r\n" +
        "Host: " + CDP_HOST + ":" + CDP_PORT + "\r\n" +
        headers.join("\r\n") + "\r\n\r\n",
    );
    if (head && head.length) upstream.write(head);
    upstream.pipe(socket);
    socket.pipe(upstream);
  });
  upstream.on("error", () => socket.destroy());
  socket.on("error", () => upstream.destroy());
  socket.on("close", () => upstream.destroy());
  upstream.on("close", () => socket.destroy());
}

function serveStatic(req, res) {
  const url = req.url.split("?")[0];
  const rel = url === "/" ? "index.html" : url.replace(/^\//, "");
  const file = path.resolve(STATIC, rel);
  if (file !== STATIC && !file.startsWith(STATIC + path.sep)) {
    res.writeHead(403);
    return res.end();
  }
  fs.readFile(file, (err, data) => {
    if (err) {
      res.writeHead(404);
      return res.end("not found");
    }
    res.setHeader(
      "Content-Type",
      MIME[path.extname(file)] || "application/octet-stream",
    );
    res.end(data);
  });
}

const server = http.createServer((req, res) => {
  const url = req.url.split("?")[0];
  if (url.startsWith("/json/")) return proxyHttp(req, res);
  return serveStatic(req, res);
});

server.on("upgrade", (req, socket, head) => {
  if (req.url.startsWith("/devtools/")) return proxyWs(req, socket, head);
  socket.destroy();
});

server.listen(PORT, LISTEN, () => {
  // eslint-disable-next-line no-console
  console.log(
    "hermes-browser-ui-gate: " +
      LISTEN + ":" + PORT + " -> " + CDP_HOST + ":" + CDP_PORT,
  );
});

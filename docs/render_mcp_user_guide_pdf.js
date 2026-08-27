#!/usr/bin/env node
"use strict";

const filesystem = require("fs");
const path = require("path");

const outputPath = path.join(__dirname, "MCP-USER-GUIDE.pdf");

const pages = [
  [
    "IOS-XR Nettools MCP",
    "User Guide for the authenticated, read-only network inspection service",
    "",
    "SERVICE OVERVIEW",
    "The MCP service runs in a persistent Docker container on a4000 and exposes",
    "native MCP streamable HTTP. It is loopback-bound by default and intended",
    "to be reached through an encrypted SSH tunnel.",
    "",
    "WHAT THE SERVICE DOES",
    "- Exposes reviewed Cisco IOS-XR lab inspection tools through MCP.",
    "- Inspects inventory, interface and routing protocol state, deterministic",
    "  device health, tickets, logs, metrics, and selected topology sources.",
    "- Reconstructs device commands from typed inputs and an allowlist.",
    "- Sanitizes raw device command text before MCP clients receive results.",
    "- Does not expose arbitrary commands, configuration mode, shell access,",
    "  or network remediation capability.",
    "- Keeps active probes disabled by default.",
    "",
    "CONNECT FROM A MAC",
    "1. Create an encrypted tunnel and keep it running:",
    "   ssh -N -L 8000:127.0.0.1:8000 rami@a4000",
    "",
    "2. Point an MCP streamable-HTTP client at:",
    "   http://127.0.0.1:8000/mcp",
    "",
    "3. Send this header with every MCP request:",
    "   Authorization: Bearer <token>",
    "",
    "Use the client's secret store or a local environment variable for the",
    "token. Never commit, paste into a shared document, or embed it in source.",
  ],
  [
    "AUTHENTICATION AND ACCESS",
    "Transport: native MCP streamable HTTP.",
    "Authentication: shared bearer token; missing or invalid tokens receive 401.",
    "Encryption: the SSH tunnel encrypts traffic between the Mac and a4000.",
    "Exposure: loopback-only on a4000 by default.",
    "Authorization: every valid token holder receives the same read surface.",
    "",
    "IMPORTANT LIMIT",
    "This is shared-secret access, not per-user identity or RBAC. Do not expose",
    "the endpoint directly to the public internet. Public deployment would need",
    "TLS termination, identity, token rotation, authorization, rate limits,",
    "and a separate deployment review.",
    "",
    "USEFUL FIRST CALLS",
    "list_lab_devices             List the nine-device lab inventory.",
    "check_lab_interfaces         Inspect interface status for a device.",
    "check_lab_bgp_neighbors      Inspect BGP neighbor state.",
    "check_lab_isis_neighbors     Inspect IS-IS adjacency state.",
    "assess_lab_device_health     Run deterministic health rules and findings.",
    "investigate_lab_session      Follow a deterministic BGP, interface,",
    "                             IS-IS, or LDP dependency ladder.",
    "list_lab_tickets             List open tickets; set include_closed=true",
    "                             to include completed investigations.",
    "",
    "CLIENT CONFIGURATION PATTERN",
    "Use the endpoint URL and bearer header required by your MCP client. A",
    "representative configuration has an MCP server URL of:",
    "   http://127.0.0.1:8000/mcp",
    "and an Authorization header sourced from a local secret variable.",
  ],
  [
    "OPERATIONAL NOTES",
    "- Router access uses strict SSH host-key verification.",
    "- Tickets, evidence, admission state, and event state use a persistent",
    "  Docker named volume and survive container restart.",
    "- Docker Compose restarts the service after host reboot.",
    "- NetBox and Neo4j use Docker service DNS when their backing services run.",
    "  A stopped backing service returns a structured MCP error.",
    "- External-source tools depend on their platform services being available.",
    "",
    "SUPPORT CHECKLIST",
    "1. Confirm the SSH tunnel is running.",
    "2. Confirm the MCP client is sending the bearer token.",
    "3. Call list_lab_devices before a device-specific tool.",
    "4. For an object refusal, refresh the relevant device check and verify",
    "   that the requested object exists in current evidence.",
    "5. For NetBox or graph errors, verify the corresponding backing service",
    "   before changing MCP configuration.",
    "",
    "CURRENT SERVICE CHARACTERISTICS",
    "- 37 MCP tools are exposed on the classic surface.",
    "- Device inspection tools are read-only and command allowlisted.",
    "- Raw command output is withheld or sanitized at the MCP boundary.",
    "- The service is reachable from a Mac through the SSH tunnel above.",
    "",
    "This guide intentionally omits bearer tokens, router credentials,",
    "and internal secret values.",
  ],
];

function escapePdfText(value) {
  return value.replace(/\\/g, "\\\\").replace(/\(/g, "\\(").replace(/\)/g, "\\)");
}

function contentStream(lines, pageNumber) {
  const text = ["BT", "/F1 11 Tf", "48 792 Td", "15 TL"];
  lines.forEach((line, index) => {
    if (index === 0) {
      text.push("/F1 18 Tf");
    } else if (index === 1 && pageNumber === 1) {
      text.push("/F1 11 Tf");
    } else if (line && line === line.toUpperCase() && line.length > 4) {
      text.push("/F1 13 Tf");
    } else {
      text.push("/F1 11 Tf");
    }
    text.push(`(${escapePdfText(line)}) Tj`);
    if (index !== lines.length - 1) text.push("T*");
  });
  text.push("ET");
  return text.join("\n");
}

const objects = [];
objects[1] = "<< /Type /Catalog /Pages 2 0 R >>";
objects[2] = `<< /Type /Pages /Kids [${pages.map((_, index) => `${3 + index * 2} 0 R`).join(" ")}] /Count ${pages.length} >>`;
pages.forEach((lines, index) => {
  const pageObject = 3 + index * 2;
  const contentObject = pageObject + 1;
  const stream = contentStream(lines, index + 1);
  objects[pageObject] = `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 ${3 + pages.length * 2} 0 R >> >> /Contents ${contentObject} 0 R >>`;
  objects[contentObject] = `<< /Length ${Buffer.byteLength(stream, "ascii")} >>\nstream\n${stream}\nendstream`;
});
objects[3 + pages.length * 2] = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>";

let pdf = "%PDF-1.4\n% MCP User Guide\n";
const offsets = [0];
for (let index = 1; index < objects.length; index += 1) {
  offsets[index] = Buffer.byteLength(pdf, "ascii");
  pdf += `${index} 0 obj\n${objects[index]}\nendobj\n`;
}
const crossReferenceOffset = Buffer.byteLength(pdf, "ascii");
pdf += `xref\n0 ${objects.length}\n0000000000 65535 f \n`;
for (let index = 1; index < objects.length; index += 1) {
  pdf += `${String(offsets[index]).padStart(10, "0")} 00000 n \n`;
}
pdf += `trailer\n<< /Size ${objects.length} /Root 1 0 R >>\nstartxref\n${crossReferenceOffset}\n%%EOF\n`;

filesystem.writeFileSync(outputPath, pdf, "ascii");
console.log(outputPath);
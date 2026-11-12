// Misst in einem echten Browser, welche Anfragen jede Seite stellt, und macht Handy-Fotos.
// Nur Node ab 22 (eingebautes WebSocket) und das Chrome-DevTools-Protokoll, keine Pakete.
// Aufruf durch pruefen.py:
//   node pruefen_browser.mjs <browser.exe> <basis-url> <fotos-ordner> <pfad> [<pfad> ...]
// Der Browser läuft kopflos mit einem Wegwerf-Profil (nie dem eigenen) und löst außer 127.0.0.1 keine
// Namen auf: Eine Anfrage nach außen würde scheitern, wird aber trotzdem gezählt.
// Ausgabe: eine JSON-Zeile je Lauf mit den Anfragen, dem Status und der Breite jeder Seite.
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const [exe, basis, fotos, ...pfade] = process.argv.slice(2);
const HANDY = { width: 390, height: 844, deviceScaleFactor: 3, mobile: true };
const warte = (ms) => new Promise((r) => setTimeout(r, ms));
const profil = mkdtempSync(join(tmpdir(), "webseite-pruefung-"));
const browser = spawn(exe, [
	"--headless=new", `--user-data-dir=${profil}`, "--remote-debugging-port=0",
	"--no-first-run", "--no-default-browser-check", "--disable-background-networking",
	"--disable-component-update", "--disable-sync", "--disable-extensions", "--disable-default-apps",
	"--disable-features=Translate,OptimizationHints,MediaRouter,AutofillServerCommunication",
	"--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1", "about:blank",
], { stdio: "ignore" });

let fertig = false;
async function ende(code) {
	if (fertig) return;
	fertig = true;
	try { browser.kill(); } catch { }
	for (let i = 0; i < 50 && browser.exitCode === null; i++) await warte(100);
	try { rmSync(profil, { recursive: true, force: true }); } catch { }
	process.exit(code);
}

try {
	let port = null, weg = null;
	for (let i = 0; i < 200 && !port; i++) {
		await warte(100);
		const f = join(profil, "DevToolsActivePort");
		if (existsSync(f)) {
			const [p, w] = readFileSync(f, "utf8").split("\n");
			if (w && w.trim()) { port = p.trim(); weg = w.trim(); }
		}
	}
	if (!port) throw new Error("Browser meldet keinen DevTools-Port");
	const ws = new WebSocket(`ws://127.0.0.1:${port}${weg}`);
	await new Promise((ok, nein) => { ws.onopen = ok; ws.onerror = () => nein(new Error("WebSocket")); });
	let id = 0;
	const offen = new Map();
	const hoerer = new Set();
	ws.onmessage = (ev) => {
		const m = JSON.parse(ev.data);
		if (m.id && offen.has(m.id)) {
			const { ok, nein } = offen.get(m.id);
			offen.delete(m.id);
			m.error ? nein(new Error(m.error.message)) : ok(m.result);
		} else if (m.method) for (const h of hoerer) h(m);
	};
	const send = (method, params = {}, sessionId) => new Promise((ok, nein) => {
		const i = ++id;
		offen.set(i, { ok, nein });
		ws.send(JSON.stringify({ id: i, method, params, sessionId }));
	});

	const { targetId } = await send("Target.createTarget", { url: "about:blank" });
	const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
	const s = (m, p) => send(m, p, sessionId);
	await s("Network.enable");
	await s("Network.setCacheDisabled", { cacheDisabled: true });
	await s("Page.enable");
	await s("Emulation.setDeviceMetricsOverride", HANDY);

	const ergebnis = [];
	for (const pfad of pfade) {
		const anfragen = [];
		let status = null, geladen = false;
		const h = (m) => {
			if (m.sessionId !== sessionId) return;
			if (m.method === "Network.requestWillBeSent") anfragen.push(m.params.request.url);
			if (m.method === "Network.responseReceived" && m.params.type === "Document") status = m.params.response.status;
			if (m.method === "Page.loadEventFired") geladen = true;
		};
		hoerer.add(h);
		await s("Page.navigate", { url: basis + pfad });
		for (let i = 0; i < 150 && !geladen; i++) await warte(100);
		await warte(1500);  // späte Anfragen abwarten
		const breite = (await s("Runtime.evaluate", {
			expression: "[document.documentElement.scrollWidth, window.innerWidth]", returnByValue: true,
		})).result.value;
		const hoehe = Math.ceil((await s("Page.getLayoutMetrics")).cssContentSize.height);
		await s("Emulation.setDeviceMetricsOverride", { ...HANDY, height: Math.max(HANDY.height, hoehe) });
		await warte(300);
		const bild = await s("Page.captureScreenshot", { format: "png" });
		await s("Emulation.setDeviceMetricsOverride", HANDY);
		const name = (pfad.replace(/^\/+|\/+$/g, "").replace(/[\/.]/g, "_") || "startseite") + ".png";
		writeFileSync(join(fotos, name), Buffer.from(bild.data, "base64"));
		hoerer.delete(h);
		ergebnis.push({ pfad, geladen, status, anfragen, breite, foto: name });
	}
	console.log(JSON.stringify(ergebnis));
	await send("Browser.close").catch(() => { });
	await ende(0);
} catch (x) {
	console.error("BROWSER-FEHLER: " + x.message);
	await ende(2);
}

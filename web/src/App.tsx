import { useEffect, useState } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { loadConfig, setRehearsal } from "./lib/api";
import { startLive } from "./lib/store";
import { Join } from "./pages/Join";
import { RoleApp } from "./pages/RoleApp";
import { Screen } from "./pages/Screen";
import { Admin } from "./pages/Admin";
import { SealSheet } from "./pages/SealSheet";

export default function App() {
  const [ready, setReady] = useState(false);
  useEffect(() => {
    startLive();
    // Rehearsal mode decides where the session is kept, so it must be known before any page reads it.
    loadConfig().then((c) => setRehearsal(c.rehearsal)).catch(() => {}).finally(() => setReady(true));
  }, []);
  if (!ready) return null;
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Join />} />
        <Route path="/app" element={<RoleApp />} />
        <Route path="/screen" element={<Screen />} />
        <Route path="/admin" element={<Admin />} />
        <Route path="/seals" element={<SealSheet />} />
      </Routes>
    </BrowserRouter>
  );
}

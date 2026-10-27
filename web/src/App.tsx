import { lazy, Suspense } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { AppProvider } from "./lib/app";
import { LeaverPage } from "./pages/Leaver";
import { SuccessorPage } from "./pages/Successor";
import { ManagerPage } from "./pages/Manager";
import { AdminPage } from "./pages/Admin";
import { DemoPage } from "./pages/Demo";
import { NotFound } from "./pages/NotFound";

// GSAP 은 소개 페이지에서만 쓴다 → 따로 나눠 불러온다.
const IntroPage = lazy(() => import("./pages/Intro"));

export function App() {
  return (
    <BrowserRouter>
      <AppProvider>
        <Routes>
          <Route path="/intro" element={<Suspense fallback={null}><IntroPage /></Suspense>} />
          <Route path="/" element={<LeaverPage />} />
          <Route path="/chat" element={<SuccessorPage />} />
          <Route path="/report" element={<ManagerPage />} />
          <Route path="/admin" element={<AdminPage />} />
          <Route path="/demo" element={<DemoPage />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </AppProvider>
    </BrowserRouter>
  );
}

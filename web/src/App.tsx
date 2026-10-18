import { Link, Route, Routes } from "react-router-dom";
import Home from "./pages/Home";
import DemoChat from "./pages/DemoChat";
import CaseView from "./pages/CaseView";

function NotFound() {
  return (
    <div className="mx-auto max-w-xl p-10 text-center">
      <p className="text-lg font-semibold">Page not found</p>
      <Link className="mt-3 inline-block text-sky-600 underline" to="/">
        Back to cases
      </Link>
    </div>
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Home />} />
      <Route path="/demo" element={<DemoChat />} />
      <Route path="/cases/:id" element={<CaseView />} />
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}

import { RouterProvider } from "react-router-dom";
import { AuthProvider } from "./auth/AuthContext";
import { appRouter } from "./router";

export function App() {
  return (
    <AuthProvider>
      <RouterProvider router={appRouter} />
    </AuthProvider>
  );
}

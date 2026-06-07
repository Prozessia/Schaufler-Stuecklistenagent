"use client";

import { AlertCircle, Eye, EyeOff, Lock, ShieldCheck, User } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getCurrentUser, login } from "@/lib/api";
import { DASHBOARD_ROUTE } from "@/lib/routes";

type LoginPayload = {
  username: string;
  password: string;
};

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    getCurrentUser()
      .then(() => {
        if (active) router.replace(DASHBOARD_ROUTE);
      })
      .catch(() => {
        // Not logged in yet — stay on login page.
      });
    return () => {
      active = false;
    };
  }, [router]);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setErrorMessage(null);
    setIsSubmitting(true);

    const payload: LoginPayload = { username: username.trim(), password };

    try {
      await login(payload);
      router.replace(DASHBOARD_ROUTE);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Login fehlgeschlagen";
      setErrorMessage(
        message.toLowerCase().includes("invalid")
          ? "Ungueltiger Benutzername oder Passwort."
          : message
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm space-y-8">
        <div className="text-center">
          <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-xl bg-[hsl(var(--primary))]/10">
            <Lock className="h-7 w-7 text-[hsl(var(--primary))]" />
          </div>
          <h1 className="text-xl font-semibold tracking-tight text-foreground">
            Stuecklistenagent
          </h1>
          <p className="mt-1 text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
            Schaufler Tooling
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4" noValidate>
          <div className="space-y-2">
            <label htmlFor="login-username" className="text-xs font-medium text-muted-foreground">
              Benutzername
            </label>
            <div className="relative">
              <User className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                id="login-username"
                name="username"
                type="text"
                autoComplete="username"
                required
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                placeholder="admin"
                className="h-10 pl-10"
              />
            </div>
          </div>

          <div className="space-y-2">
            <label htmlFor="login-password" className="text-xs font-medium text-muted-foreground">
              Passwort
            </label>
            <div className="relative">
              <Lock className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                id="login-password"
                name="password"
                type={showPassword ? "text" : "password"}
                autoComplete="current-password"
                required
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder="Passwort"
                className="h-10 pl-10 pr-10"
              />
              <button
                type="button"
                aria-label={showPassword ? "Passwort verbergen" : "Passwort anzeigen"}
                onClick={() => setShowPassword((prev) => !prev)}
                className="absolute right-2 top-1/2 inline-flex h-7 w-7 -translate-y-1/2 items-center justify-center rounded-md text-muted-foreground transition-colors hover:text-foreground"
              >
                {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
            </div>
          </div>

          {errorMessage && (
            <div
              className="flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive"
              role="alert"
              aria-live="polite"
            >
              <AlertCircle className="h-4 w-4 shrink-0" />
              {errorMessage}
            </div>
          )}

          <Button type="submit" disabled={isSubmitting} className="h-10 w-full">
            {isSubmitting ? "Anmelden..." : "Anmelden"}
          </Button>
        </form>

        <p className="flex items-center justify-center gap-1.5 text-center text-xs text-muted-foreground">
          <ShieldCheck className="h-3.5 w-3.5" />
          Nur fuer autorisierte Mitarbeitende · Zugriff wird protokolliert
        </p>
      </div>
    </div>
  );
}

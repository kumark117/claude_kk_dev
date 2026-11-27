import AskForm from "../components/AskForm";

export default function Page() {
  return (
    <main style={{ maxWidth: "40rem", margin: "2rem auto", padding: "0 1rem" }}>
      <h1>Ask the docs</h1>
      <AskForm />
    </main>
  );
}

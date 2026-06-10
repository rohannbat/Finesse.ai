import SwiftUI

/// Quick day-totals logger: four numeric fields, one Save.
/// Replace semantics — saving sets today's totals outright (re-open and
/// re-save with running totals to update during the day).
struct NutritionLogSheet: View {
    @Environment(\.dismiss) private var dismiss

    let snapshot: Snapshot?
    let onSaved: (SyncResponse) -> Void

    @State private var calories = ""
    @State private var protein = ""
    @State private var carbs = ""
    @State private var fat = ""
    @State private var isSaving = false
    @State private var errorMessage: String?

    var body: some View {
        NavigationStack {
            Form {
                Section("Today's totals") {
                    numericField("Calories (kcal)", text: $calories)
                    numericField("Protein (g)", text: $protein)
                    numericField("Carbs (g) — optional", text: $carbs)
                    numericField("Fat (g) — optional", text: $fat)
                }
                if let errorMessage {
                    Section {
                        Text(errorMessage).foregroundStyle(.red)
                    }
                }
                Section {
                    Button(action: save) {
                        if isSaving {
                            ProgressView().frame(maxWidth: .infinity)
                        } else {
                            Text("Save").frame(maxWidth: .infinity)
                        }
                    }
                    .disabled(isSaving || Int(calories) == nil || Double(protein) == nil)
                } footer: {
                    Text("Sets the day's totals (doesn't add to them). Log once at the end of the day, or update with running totals as you go.")
                }
            }
            .navigationTitle("Log food")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
            }
            .onAppear(perform: prefill)
        }
        .presentationDetents([.medium])
    }

    private func numericField(_ label: String, text: Binding<String>) -> some View {
        TextField(label, text: text)
            .keyboardType(.decimalPad)
    }

    private func prefill() {
        guard calories.isEmpty else { return }
        if let logged = snapshot?.caloriesConsumed { calories = "\(logged)" }
        if let logged = snapshot?.proteinG { protein = "\(Int(logged))" }
        if let logged = snapshot?.carbsG { carbs = "\(Int(logged))" }
        if let logged = snapshot?.fatG { fat = "\(Int(logged))" }
    }

    private func save() {
        guard let kcal = Int(calories), let proteinG = Double(protein) else { return }
        isSaving = true
        errorMessage = nil
        Task {
            defer { isSaving = false }
            do {
                let response = try await APIClient.shared.logNutrition(
                    calories: kcal,
                    proteinG: proteinG,
                    carbsG: Double(carbs),   // nil if empty/non-numeric
                    fatG: Double(fat)
                )
                onSaved(response)
                dismiss()
            } catch {
                errorMessage = error.localizedDescription
            }
        }
    }
}

#Preview {
    NutritionLogSheet(snapshot: nil) { _ in }
}

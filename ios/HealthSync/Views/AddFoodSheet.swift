import SwiftUI

/// Per-meal quick logger: name + calories (macros optional), one Save.
/// The day's totals roll up automatically from all logged entries.
struct AddFoodSheet: View {
    @Environment(\.dismiss) private var dismiss

    let onSaved: (FoodLogResponse) -> Void

    @State private var name = ""
    @State private var calories = ""
    @State private var protein = ""
    @State private var carbs = ""
    @State private var fat = ""
    @State private var isSaving = false
    @State private var errorMessage: String?

    var body: some View {
        NavigationStack {
            Form {
                Section("What did you eat?") {
                    TextField("e.g. Chicken and rice", text: $name)
                    TextField("Calories (kcal)", text: $calories)
                        .keyboardType(.numberPad)
                    TextField("Protein (g) — optional", text: $protein)
                        .keyboardType(.decimalPad)
                    TextField("Carbs (g) — optional", text: $carbs)
                        .keyboardType(.decimalPad)
                    TextField("Fat (g) — optional", text: $fat)
                        .keyboardType(.decimalPad)
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
                    .disabled(isSaving || name.trimmingCharacters(in: .whitespaces).isEmpty || Int(calories) == nil)
                } footer: {
                    Text("Each save adds an entry; the day's totals are the sum of your entries.")
                }
            }
            .navigationTitle("Add food")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
            }
        }
        .presentationDetents([.medium])
    }

    private func save() {
        guard let kcal = Int(calories) else { return }
        isSaving = true
        errorMessage = nil
        Task {
            defer { isSaving = false }
            do {
                let response = try await APIClient.shared.addFood(
                    name: name.trimmingCharacters(in: .whitespaces),
                    calories: kcal,
                    proteinG: Double(protein),  // nil if empty/non-numeric
                    carbsG: Double(carbs),
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
    AddFoodSheet { _ in }
}

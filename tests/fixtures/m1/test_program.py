import math

# Solve ax² + bx + c = 0
a = 2
b = -7
c = 3

print("QUADRATIC EQUATION SOLVER")
print(f"Equation: {a}x² + ({b})x + ({c}) = 0")

# Calculate discriminant
D = b**2 - 4 * a * c

print(f"\nDiscriminant: {D}")

if a == 0:
    print("This is not a quadratic equation.")

elif D > 0:
    root1 = (-b + math.sqrt(D)) / (2 * a)
    root2 = (-b - math.sqrt(D)) / (2 * a)

    print("Two distinct real roots:")
    print(f"Root 1 = {root1}")
    print(f"Root 2 = {root2}")

    print("\nVerification:")
    print(f"f({root1}) = {a*root1**2 + b*root1 + c}")
    print(f"f({root2}) = {a*root2**2 + b*root2 + c}")

elif D == 0:
    root = -b / (2 * a)
    print(f"One repeated real root: {root}")

else:
    real = -b / (2 * a)
    imaginary = math.sqrt(-D) / (2 * a)
    print(f"Complex roots: {real} ± {imaginary}i")

print("\nCalculation completed!")